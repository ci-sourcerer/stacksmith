import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..change_reports import write_change_report
from .contracts import CiExecutionManifest, CiExecutionPhase

_INFRASTRUCTURE_PHASES = {"plan", "apply", "destroy"}


def _read_report(
    path: Path, report_name: str, issues: list[dict[str, str]]
) -> dict[str, Any] | None:
    if not path.is_file():
        issues.append(
            {
                "code": "missing_report",
                "report": report_name,
                "detail": f"Expected report was not written: {path}",
            }
        )
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        issues.append(
            {
                "code": "invalid_report_json",
                "report": report_name,
                "detail": f"Report is not valid JSON: {exc}",
            }
        )
        return None
    except OSError as exc:
        issues.append(
            {
                "code": "unreadable_report",
                "report": report_name,
                "detail": f"Report could not be read: {exc}",
            }
        )
        return None
    if not isinstance(report, dict):
        issues.append(
            {
                "code": "invalid_report_type",
                "report": report_name,
                "detail": "Report root must be a JSON object.",
            }
        )
        return None
    return report


def _report_source(path: Path, report: dict[str, Any] | None) -> dict[str, Any]:
    return {"path": str(path), "available": report is not None}


def _plan_reports(
    artifact_dir: Path,
    validation_report_path: Path,
    issues: list[dict[str, str]],
) -> tuple[dict[str, dict[str, Any] | None], dict[str, dict[str, Any]]]:
    plan_path = artifact_dir / "plan-summary.json"
    plan_report = _read_report(plan_path, "plan_summary", issues)
    validation_report = _read_report(
        validation_report_path, "validation_report", issues
    )
    reports = {
        "plan_summary": plan_report,
        "validation_report": validation_report,
    }
    sources = {
        "plan_summary": _report_source(plan_path, plan_report),
        "validation_report": _report_source(validation_report_path, validation_report),
    }
    for report_name, report in reports.items():
        if report is not None and not (
            isinstance(report.get("run_id"), str) and report["run_id"]
        ):
            issues.append(
                {
                    "code": "missing_report_run_id",
                    "report": report_name,
                    "detail": "Embedded report does not identify its invocation.",
                }
            )
    run_ids = {
        report["run_id"]
        for report in reports.values()
        if report is not None
        and isinstance(report.get("run_id"), str)
        and report["run_id"]
    }
    if len(run_ids) > 1:
        issues.append(
            {
                "code": "report_run_id_mismatch",
                "report": "plan_summary,validation_report",
                "detail": "Embedded reports were produced by different invocations.",
            }
        )
    return reports, sources


def _apply_reports(
    artifact_dir: Path, issues: list[dict[str, str]]
) -> tuple[dict[str, dict[str, Any] | None], dict[str, dict[str, Any]]]:
    apply_path = artifact_dir / "apply-summary.json"
    apply_report = _read_report(apply_path, "apply_summary", issues)
    if apply_report is not None and not (
        isinstance(apply_report.get("run_id"), str) and apply_report["run_id"]
    ):
        issues.append(
            {
                "code": "missing_report_run_id",
                "report": "apply_summary",
                "detail": "Embedded report does not identify its invocation.",
            }
        )
    return (
        {"apply_summary": apply_report},
        {"apply_summary": _report_source(apply_path, apply_report)},
    )


def _report_run_id(
    reports: dict[str, dict[str, Any] | None],
) -> str:
    for report in reports.values():
        if (
            report is not None
            and isinstance(report.get("run_id"), str)
            and report["run_id"]
        ):
            return report["run_id"]
    return str(uuid4())


def clear_ci_execution_reports(
    manifest: CiExecutionManifest,
    environment: str,
    phase: CiExecutionPhase,
) -> None:
    """Remove reports that could be mistaken for the current CI phase.

    Args:
        manifest: Provider-neutral CI execution manifest.
        environment: Environment executed by this CI job.
        phase: Resolved lifecycle phase.
    """
    if phase not in _INFRASTRUCTURE_PHASES:
        return
    artifact_dir = Path(manifest.workdir) / ".stacksmith-ci" / environment
    (artifact_dir / "stacksmith-report.json").unlink(missing_ok=True)
    (
        artifact_dir
        / ("plan-summary.json" if phase == "plan" else "apply-summary.json")
    ).unlink(missing_ok=True)


def write_ci_execution_report(
    manifest: CiExecutionManifest,
    environment: str,
    phase: CiExecutionPhase,
    exit_code: int,
    validation_report_path: Path | None = None,
) -> Path | None:
    """Bundle infrastructure reports for a single managed CI phase.

    Args:
        manifest: Provider-neutral CI execution manifest.
        environment: Environment executed by this CI job.
        phase: Resolved lifecycle phase.
        exit_code: Infrastructure command exit code.
        validation_report_path: Plan validation report destination.

    Returns:
        Umbrella report path, or `None` for phases without infrastructure changes.
    """
    if phase not in _INFRASTRUCTURE_PHASES:
        return None
    artifact_dir = Path(manifest.workdir) / ".stacksmith-ci" / environment
    issues: list[dict[str, str]] = []
    if phase == "plan":
        reports, sources = _plan_reports(
            artifact_dir,
            validation_report_path
            or artifact_dir / f"validation-report.{manifest.validation_report_format}",
            issues,
        )
    else:
        reports, sources = _apply_reports(artifact_dir, issues)
    output_path = artifact_dir / "stacksmith-report.json"
    write_change_report(
        {
            "schema_version": 1,
            "report_type": "stacksmith_ci_execution",
            "run_id": _report_run_id(reports),
            "command": manifest.command,
            "phase": phase,
            "environment": environment,
            "generated_at": datetime.now(UTC).isoformat(),
            "exit_code": int(exit_code),
            "complete": not issues
            and all(
                report is not None and report.get("complete", True) is not False
                for report in reports.values()
            ),
            "issues": issues,
            "sources": sources,
            "reports": reports,
        },
        output_path,
    )
    return output_path
