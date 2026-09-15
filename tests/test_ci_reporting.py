import json
from pathlib import Path

from stacksmith.ci.contracts import CiExecutionManifest
from stacksmith.ci.reporting import (
    clear_ci_execution_reports,
    write_ci_execution_report,
)


def _manifest(tmp_path: Path, command: str = "plan") -> CiExecutionManifest:
    return CiExecutionManifest(
        command=command,
        config_ref="config.yaml",
        workdir=str(tmp_path),
    )


def test_plan_ci_report_embeds_linked_plan_and_validation_reports(tmp_path: Path):
    artifact_dir = tmp_path / ".stacksmith-ci" / "dev"
    artifact_dir.mkdir(parents=True)
    plan_report = {"run_id": "run-1", "complete": True, "totals": {"create": 2}}
    validation_report = {
        "run_id": "run-1",
        "status": "pass",
        "summary": {"pass": 3, "warn": 0, "fail": 0},
    }
    (artifact_dir / "plan-summary.json").write_text(json.dumps(plan_report))
    validation_path = artifact_dir / "validation-report.json"
    validation_path.write_text(json.dumps(validation_report))

    output_path = write_ci_execution_report(
        _manifest(tmp_path), "dev", "plan", 0, validation_path
    )

    assert output_path == artifact_dir / "stacksmith-report.json"
    payload = json.loads(output_path.read_text())
    assert payload["schema_version"] == 1
    assert payload["report_type"] == "stacksmith_ci_execution"
    assert payload["run_id"] == "run-1"
    assert payload["command"] == payload["phase"] == "plan"
    assert payload["environment"] == "dev"
    assert payload["exit_code"] == 0
    assert payload["complete"] is True
    assert payload["issues"] == []
    assert payload["reports"] == {
        "plan_summary": plan_report,
        "validation_report": validation_report,
    }
    assert all(source["available"] for source in payload["sources"].values())


def test_apply_ci_report_records_missing_child_report(tmp_path: Path):
    output_path = write_ci_execution_report(
        _manifest(tmp_path, "apply"), "dev", "apply", 1
    )

    assert output_path is not None
    payload = json.loads(output_path.read_text())
    assert payload["complete"] is False
    assert payload["reports"] == {"apply_summary": None}
    assert payload["sources"]["apply_summary"]["available"] is False
    assert payload["issues"][0]["code"] == "missing_report"


def test_plan_ci_report_flags_mismatched_child_run_ids(tmp_path: Path):
    artifact_dir = tmp_path / ".stacksmith-ci" / "dev"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "plan-summary.json").write_text(
        json.dumps({"run_id": "plan-run", "complete": True})
    )
    validation_path = artifact_dir / "validation-report.json"
    validation_path.write_text(json.dumps({"run_id": "validation-run"}))

    output_path = write_ci_execution_report(
        _manifest(tmp_path), "dev", "plan", 0, validation_path
    )

    assert output_path is not None
    payload = json.loads(output_path.read_text())
    assert payload["complete"] is False
    assert payload["issues"][0]["code"] == "report_run_id_mismatch"


def test_clear_ci_reports_prevents_stale_phase_data(tmp_path: Path):
    artifact_dir = tmp_path / ".stacksmith-ci" / "dev"
    artifact_dir.mkdir(parents=True)
    for filename in ("stacksmith-report.json", "apply-summary.json"):
        (artifact_dir / filename).write_text("{}")
    (artifact_dir / "plan-summary.json").write_text("{}")

    clear_ci_execution_reports(_manifest(tmp_path, "apply"), "dev", "apply")

    assert not (artifact_dir / "stacksmith-report.json").exists()
    assert not (artifact_dir / "apply-summary.json").exists()
    assert (artifact_dir / "plan-summary.json").exists()


def test_non_infrastructure_phase_has_no_umbrella_report(tmp_path: Path):
    assert (
        write_ci_execution_report(_manifest(tmp_path), "dev", "plan-operation", 0)
        is None
    )
