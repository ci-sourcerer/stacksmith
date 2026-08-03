import logging
from pathlib import Path

import pytest

from stacksmith.api import (
    inspect_environments,
    prepare_ci_execution,
    validate_ci_inputs,
)
from stacksmith.ci.adapters import (
    manifest_output_json,
    prepare_ci_manifest_from_env,
)
from stacksmith.ci.contracts import (
    CiExecutionManifest,
    CiExecutionRow,
    build_ci_execution_argv,
)
from stacksmith.exceptions import StacksmithConfigError, StacksmithError

_FIXTURES = Path(__file__).parent / "fixtures"
_REMOTE_BACKEND_CONFIG = _FIXTURES / "sample_config.yaml"
_LOCAL_BACKEND_CONFIG = _FIXTURES / "sample_config_local.yaml"


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


def test_inspect_environments_auto_discovers_env_files_layout(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
):
    (tmp_path / "common").mkdir()
    (tmp_path / "common" / "stacksmith.yaml").write_text("merge_mode: deep\n")
    (tmp_path / "environments").mkdir()
    (tmp_path / "environments" / "dev.yaml").write_text("stacks: []\n")
    (tmp_path / "environments" / "prod.yaml").write_text("stacks: []\n")

    with caplog.at_level(logging.INFO, logger="stacksmith.gitops"):
        payload = inspect_environments(
            gitops_root=str(tmp_path),
            discovery_mode="auto",
        )

    assert payload["discovery_mode"] == "env-files"
    assert payload["selected_environments"] == ["dev", "prod"]
    assert "Auto-detected GitOps discovery mode=env-files" in caplog.messages
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


def test_inspect_environments_auto_discovers_flat_files_layout(tmp_path: Path):
    (tmp_path / "stacksmith.dev.yaml").write_text("stacks: []\n")
    (tmp_path / "stacksmith.prod.yaml").write_text("stacks: []\n")

    payload = inspect_environments(
        gitops_root=str(tmp_path),
        discovery_mode="auto",
    )

    assert payload["discovery_mode"] == "flat-files"
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
        config_ref=str(_REMOTE_BACKEND_CONFIG),
        gitops_root=str(tmp_path),
        discovery_mode="env-files",
        event_name="push",
        changed_paths=["environments/dev.yaml"],
        ref_name="main",
        default_branch="main",
        stacksmith_args_json='["--tag", "web"]',
        debug=True,
        no_cas=True,
        locked=True,
        offline=True,
        lockfile="stacksmith.lock.yaml",
        fail_on_changes=True,
    )

    assert manifest.version == 1
    assert manifest.stacksmith_args == ["--tag", "web"]
    assert manifest.debug is True
    assert manifest.no_cas is True
    assert manifest.locked is True
    assert manifest.offline is True
    assert manifest.lockfile == "stacksmith.lock.yaml"
    assert [row.model_dump() for row in manifest.matrix] == [
        {
            "environment": "dev",
            "runfile": f"{tmp_path.as_posix()}/common/stacksmith.yaml",
            "environment_runfile": f"{tmp_path.as_posix()}/environments/dev.yaml",
        }
    ]


def test_prepare_ci_execution_rejects_local_backend(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    with pytest.raises(
        StacksmithConfigError,
        match="CI prepare rejected environment 'dev': the local backend",
    ):
        prepare_ci_execution(
            command="plan",
            config_ref=str(_LOCAL_BACKEND_CONFIG),
            gitops_root=str(tmp_path),
            discovery_mode="env-files",
            environments="dev",
            skip_branch_validation=True,
        )


def test_prepare_ci_execution_rejects_local_backend_from_runfile(tmp_path: Path):
    _create_env_files_layout(tmp_path)
    (tmp_path / "common" / "stacksmith.yaml").write_text(
        f"configs:\n  - source: local\n    data:\n      path: {_LOCAL_BACKEND_CONFIG}\n"
    )
    config_overlay = tmp_path / "platform-overlay.yaml"
    config_overlay.write_text("description: Platform overlay.\n")

    with pytest.raises(StacksmithConfigError, match="local backend is not supported"):
        prepare_ci_execution(
            command="plan",
            config_ref=str(config_overlay),
            gitops_root=str(tmp_path),
            discovery_mode="env-files",
            environments="dev",
            skip_branch_validation=True,
        )


def test_manifest_output_json_is_compact():
    manifest = CiExecutionManifest(
        command="plan",
        config_ref="platform/stacksmith-config.yaml",
        matrix=[
            CiExecutionRow(
                environment="dev",
                runfile="common/stacksmith.yaml",
            )
        ],
    )

    output = manifest_output_json(manifest)

    assert "\n" not in output
    assert ": " not in output
    assert ", " not in output


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


@pytest.mark.parametrize(
    "stacksmith_args_json",
    ['["--runfile", "other.yaml"]', '["--runfile=other.yaml"]'],
)
def test_prepare_ci_execution_rejects_managed_runfile_override(
    stacksmith_args_json: str, tmp_path: Path
):
    _create_env_files_layout(tmp_path)

    with pytest.raises(StacksmithConfigError, match="CI-managed runfiles"):
        prepare_ci_execution(
            command="plan",
            config_ref=str(_REMOTE_BACKEND_CONFIG),
            gitops_root=str(tmp_path),
            discovery_mode="env-files",
            stacksmith_args_json=stacksmith_args_json,
        )


@pytest.mark.parametrize(
    "stacksmith_args_json",
    [
        '["--locked"]',
        '["--offline"]',
        '["--lockfile", "other.lock.yaml"]',
        '["--lockfile=other.lock.yaml"]',
    ],
)
def test_prepare_ci_execution_rejects_managed_lock_policy_override(
    stacksmith_args_json: str, tmp_path: Path
):
    _create_env_files_layout(tmp_path)

    with pytest.raises(StacksmithConfigError, match="managed lock policy"):
        prepare_ci_execution(
            command="plan",
            config_ref="platform/stacksmith-config.yaml",
            gitops_root=str(tmp_path),
            discovery_mode="env-files",
            stacksmith_args_json=stacksmith_args_json,
        )


def test_prepare_ci_execution_rejects_offline_without_locked(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    with pytest.raises(ValueError, match="requires locked"):
        prepare_ci_execution(
            command="plan",
            config_ref="platform/stacksmith-config.yaml",
            gitops_root=str(tmp_path),
            discovery_mode="env-files",
            locked=False,
            offline=True,
        )


def test_prepare_ci_manifest_from_env_reads_debug_and_lock_settings(
    monkeypatch, tmp_path: Path
):
    _create_env_files_layout(tmp_path)
    monkeypatch.setenv("INPUT_COMMAND", "plan")
    monkeypatch.setenv("INPUT_CONFIG_REF", str(_REMOTE_BACKEND_CONFIG))
    monkeypatch.setenv("INPUT_GITOPS_ROOT", str(tmp_path))
    monkeypatch.setenv("INPUT_DISCOVERY_MODE", "env-files")
    monkeypatch.setenv("INPUT_DEBUG", "true")
    monkeypatch.setenv("INPUT_LOCKED", "true")
    monkeypatch.setenv("INPUT_OFFLINE", "true")
    monkeypatch.setenv("INPUT_LOCKFILE", "locks/stacksmith.lock.yaml")
    monkeypatch.setenv("SKIP_BRANCH_VALIDATION", "true")

    manifest = prepare_ci_manifest_from_env()

    assert manifest.debug is True
    assert manifest.locked is True
    assert manifest.offline is True
    assert manifest.lockfile == "locks/stacksmith.lock.yaml"


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
        "config_ref": str(_REMOTE_BACKEND_CONFIG),
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
    assert "INPUT_DEBUG" in actions_workflow
    assert "INPUT_DEBUG" in jenkins_pipeline
    assert "      config_ref:" not in actions_workflow
    assert "      locked:" not in actions_workflow
    assert "      offline:" not in actions_workflow
    assert "      lockfile:" not in actions_workflow
    assert "INPUT_CONFIG_REF: ${{ fromJson(toJson(vars)).STACKSMITH_CONFIG_REF" in (
        actions_workflow
    )
    assert "STACKSMITH_REQUIRE_LOCKFILE" in actions_workflow
    assert "STACKSMITH_REQUIRE_LOCKFILE" in jenkins_pipeline
    assert "string(name: 'CONFIG_REF'" not in jenkins_pipeline
    assert "booleanParam(name: 'REQUIRE_LOCKFILE'" not in jenkins_pipeline
    assert "booleanParam(name: 'OFFLINE'" not in jenkins_pipeline
    assert "string(name: 'LOCKFILE'" not in jenkins_pipeline
    plan_stage = jenkins_pipeline.index("stage('Plan')")
    approval_stage = jenkins_pipeline.index("stage('Approve')")
    apply_stage = jenkins_pipeline.index("stage('Apply')")
    assert plan_stage < approval_stage < apply_stage
    assert "setManifestCommand" not in jenkins_pipeline
    assert "import org.jenkinsci.plugins.pipeline.modeldefinition.Utils" in (
        jenkins_pipeline
    )
    assert jenkins_pipeline.count("Utils.markStageSkippedForConditional") == 4
    assert '"STACKSMITH_CI_PHASE=${command}"' in jenkins_pipeline
    assert '--phase "$STACKSMITH_CI_PHASE"' in jenkins_pipeline
    assert "inputs.command == 'plan' || inputs.command == 'apply'" in actions_workflow
    assert "needs: [discover, plan]" in actions_workflow
    assert "phase: plan" in actions_workflow
    assert "phase: apply" in actions_workflow
    assert "inputs.phase || fromJson(inputs.ci_manifest).command" in actions_executor
    assert '--phase "$STACKSMITH_CI_PHASE"' in actions_executor


def test_ci_plan_execution_only_writes_redacted_plan_json():
    argv = build_ci_execution_argv(
        CiExecutionManifest(
            command="plan",
            config_ref="platform/stacksmith-config.yaml",
            matrix=[
                CiExecutionRow(
                    environment="dev",
                    runfile="common/stacksmith.yaml",
                )
            ],
        ),
        "dev",
    )

    assert "--save-redacted-plan-json" in argv
    assert "--save-plan-json" not in argv
    assert argv[argv.index("--save-redacted-plan-json") + 1] == (
        ".stacksmith-ci/dev/plan.json"
    )


def test_ci_plan_execution_includes_debug_and_lock_options():
    argv = build_ci_execution_argv(
        CiExecutionManifest(
            command="plan",
            config_ref="platform/stacksmith-config.yaml",
            debug=True,
            locked=True,
            offline=True,
            lockfile="locks/stacksmith.lock.yaml",
            matrix=[
                CiExecutionRow(
                    environment="dev",
                    runfile="common/stacksmith.yaml",
                )
            ],
        ),
        "dev",
    )

    assert "--debug" in argv
    assert "--locked" in argv
    assert "--offline" in argv
    assert argv[argv.index("--lockfile") + 1] == "locks/stacksmith.lock.yaml"


def test_ci_apply_manifest_supports_plan_then_apply_phases():
    manifest = CiExecutionManifest(
        command="apply",
        config_ref="platform/stacksmith-config.yaml",
        fail_on_changes=True,
        matrix=[CiExecutionRow(environment="dev", runfile="common/stacksmith.yaml")],
    )

    plan_argv = build_ci_execution_argv(manifest, "dev", "plan")
    apply_argv = build_ci_execution_argv(manifest, "dev", "apply")

    assert plan_argv[0] == "plan"
    assert "--save-redacted-plan-json" in plan_argv
    assert "--fail-on-changes" not in plan_argv
    assert apply_argv[0] == "apply"
    assert "--auto-approve" in apply_argv


def test_ci_manifest_rejects_unapproved_execution_phase():
    manifest = CiExecutionManifest(
        command="plan",
        config_ref="platform/stacksmith-config.yaml",
        matrix=[CiExecutionRow(environment="dev", runfile="common/stacksmith.yaml")],
    )

    with pytest.raises(StacksmithError, match="cannot execute phase 'apply'"):
        build_ci_execution_argv(manifest, "dev", "apply")
