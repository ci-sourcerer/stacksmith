from pathlib import Path

import pytest
from stacksmith.api import inspect_environments, validate_ci_inputs


def _create_env_files_layout(tmp_path: Path) -> None:
    (tmp_path / "common").mkdir()
    (tmp_path / "common" / "stacksmith.yaml").write_text("merge_mode: deep\n")
    (tmp_path / "environments").mkdir()
    (tmp_path / "environments" / "dev.yaml").write_text("stacks: []\n")
    (tmp_path / "environments" / "prod.yaml").write_text("stacks: []\n")


def test_validate_ci_inputs_passes_for_valid_layout(tmp_path: Path):
    (tmp_path / "common").mkdir()
    (tmp_path / "common" / "stacksmith.yaml").write_text("merge_mode: deep\n")
    (tmp_path / "environments").mkdir()
    (tmp_path / "environments" / "dev").mkdir()

    report = validate_ci_inputs(
        gitops_root=str(tmp_path),
        discovery_mode="folders",
        env_file="/dev/null",
        validation_report_format="json",
    )

    assert report["status"] == "pass"
    assert report["exit_code"] == 0


def test_inspect_environments_auto_discovers_env_files_layout(tmp_path: Path):
    (tmp_path / "common").mkdir()
    (tmp_path / "common" / "stacksmith.yaml").write_text("merge_mode: deep\n")
    (tmp_path / "environments").mkdir()
    (tmp_path / "environments" / "dev.yaml").write_text("stacks: []\n")
    (tmp_path / "environments" / "prod.yaml").write_text("stacks: []\n")

    payload = inspect_environments(
        gitops_root=str(tmp_path),
        discovery_mode="auto",
    )

    assert payload["discovery_mode"] == "env-files"
    assert payload["selected_environments"] == ["dev", "prod"]


def test_validate_ci_inputs_auto_discovers_env_files_layout(tmp_path: Path):
    (tmp_path / "common").mkdir()
    (tmp_path / "common" / "stacksmith.yaml").write_text("merge_mode: deep\n")
    (tmp_path / "environments").mkdir()
    (tmp_path / "environments" / "dev.yaml").write_text("stacks: []\n")
    (tmp_path / "environments" / "prod.yaml").write_text("stacks: []\n")

    report = validate_ci_inputs(
        gitops_root=str(tmp_path),
        env_file="/dev/null",
        validation_report_format="json",
    )

    assert report["status"] == "pass"
    assert report["exit_code"] == 0
    assert report["results"][0]["detail"]["discovery_mode"] == "env-files"


def test_validate_ci_inputs_fails_for_missing_env_file(tmp_path: Path):
    (tmp_path / "common").mkdir()
    (tmp_path / "common" / "stacksmith.yaml").write_text("merge_mode: deep\n")
    (tmp_path / "environments").mkdir()
    (tmp_path / "environments" / "dev").mkdir()

    report = validate_ci_inputs(
        gitops_root=str(tmp_path),
        discovery_mode="folders",
        env_file=str(tmp_path / "missing.env"),
        validation_report_format="json",
    )

    assert report["status"] == "fail"
    assert report["exit_code"] == 1


def test_validate_ci_inputs_fails_for_invalid_validation_report_format(tmp_path: Path):
    (tmp_path / "common").mkdir()
    (tmp_path / "common" / "stacksmith.yaml").write_text("merge_mode: deep\n")
    (tmp_path / "environments").mkdir()
    (tmp_path / "environments" / "dev").mkdir()

    report = validate_ci_inputs(
        gitops_root=str(tmp_path),
        discovery_mode="folders",
        env_file="/dev/null",
        validation_report_format="markdown",
    )

    assert report["status"] == "fail"
    assert report["exit_code"] == 1


def test_inspect_environments_manual_unknown_environment_fails(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    with pytest.raises(ValueError, match="Unknown manual environment"):
        inspect_environments(
            gitops_root=str(tmp_path),
            discovery_mode="env-files",
            environments="dev,staging",
        )


def test_inspect_environments_push_no_matches_returns_empty_selection(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    payload = inspect_environments(
        gitops_root=str(tmp_path),
        discovery_mode="env-files",
        event_name="push",
        changed_paths=["docs/readme.md"],
    )

    assert payload["selected_environments"] == []
    assert payload["matrix"] == []
