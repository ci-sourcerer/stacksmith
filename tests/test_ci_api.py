from pathlib import Path

import pytest
from stacksmith.api import (
    inspect_environments,
    prepare_ci_execution,
    validate_ci_inputs,
)
from stacksmith.exceptions import StacksmithConfigError


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


def test_prepare_ci_execution_returns_provider_neutral_manifest(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    manifest = prepare_ci_execution(
        command="plan",
        config_ref="platform/stacksmith-config.yaml",
        gitops_root=str(tmp_path),
        discovery_mode="env-files",
        event_name="push",
        changed_paths=["environments/dev.yaml"],
        ref_name="main",
        default_branch="main",
        stacksmith_args_json='["--tag", "web"]',
        no_cas=True,
        fail_on_changes=True,
    )

    assert manifest.version == 1
    assert manifest.stacksmith_args == ["--tag", "web"]
    assert manifest.no_cas is True
    assert [row.model_dump() for row in manifest.matrix] == [
        {
            "environment": "dev",
            "runfile": f"{tmp_path.as_posix()}/common/stacksmith.yaml",
            "environment_runfile": f"{tmp_path.as_posix()}/environments/dev.yaml",
        }
    ]


def test_prepare_ci_execution_rejects_managed_config_override(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    with pytest.raises(StacksmithConfigError, match="cannot override"):
        prepare_ci_execution(
            command="plan",
            config_ref="platform/stacksmith-config.yaml",
            gitops_root=str(tmp_path),
            discovery_mode="env-files",
            stacksmith_args_json='["--config", "other.yaml"]',
        )


def test_prepare_ci_execution_applies_shared_pull_request_policy(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    with pytest.raises(StacksmithConfigError, match="not allowed on pull requests"):
        prepare_ci_execution(
            command="apply",
            config_ref="platform/stacksmith-config.yaml",
            gitops_root=str(tmp_path),
            discovery_mode="env-files",
            event_name="pull_request",
            base_ref="main",
            default_branch="main",
        )


def test_prepare_ci_execution_has_identical_provider_normalized_output(
    tmp_path: Path,
):
    _create_env_files_layout(tmp_path)
    common_inputs = {
        "command": "plan",
        "config_ref": "platform/stacksmith-config.yaml",
        "gitops_root": str(tmp_path),
        "discovery_mode": "env-files",
        "event_name": "push",
        "changed_paths": ["environments/dev.yaml"],
        "ref_name": "main",
        "default_branch": "main",
    }

    github_manifest = prepare_ci_execution(**common_inputs)
    jenkins_manifest = prepare_ci_execution(
        **common_inputs,
        is_primary_branch=True,
    )

    assert github_manifest.model_dump() == jenkins_manifest.model_dump()


def test_ci_workflow_adapters_delegate_to_manifest_contract():
    repository_root = Path(__file__).parents[1]
    actions_workflow = (
        repository_root / ".github/workflows/stacksmith-gitops-opinionated-reusable.yml"
    ).read_text()
    actions_executor = (
        repository_root / ".github/workflows/stacksmith-gitops-reusable.yml"
    ).read_text()
    jenkins_pipeline = (repository_root / "Jenkinsfile").read_text()

    assert "stacksmith ci prepare-from-env" in actions_workflow
    assert "stacksmith ci execute-from-env" in actions_executor
    assert "stacksmith ci prepare-from-env" in jenkins_pipeline
    assert "stacksmith ci execute-from-env" in jenkins_pipeline
