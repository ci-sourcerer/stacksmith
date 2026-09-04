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


def test_inspect_environments_push_unmapped_change_selects_all(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    payload = inspect_environments(
        gitops_root=str(tmp_path),
        discovery_mode="env-files",
        event_name="push",
        changed_paths=["docs/readme.md"],
    )

    assert payload["selected_environments"] == ["dev", "prod"]
    assert [row["environment"] for row in payload["matrix"]] == ["dev", "prod"]


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

    assert manifest.version == 2
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


def test_prepare_ci_execution_builds_version_two_operation_batch(
    monkeypatch, tmp_path: Path
):
    _create_env_files_layout(tmp_path)
    monkeypatch.setenv("STACKSMITH_MAX_PARALLEL_OPERATIONS", "2")

    manifest = prepare_ci_execution(
        command="apply-operation",
        operation_names="publish, deploy,verify",
        config_ref=str(_REMOTE_BACKEND_CONFIG),
        gitops_root=str(tmp_path),
        discovery_mode="env-files",
        environments="dev",
        skip_branch_validation=True,
    )

    assert manifest.version == 2
    assert manifest.operation_names == ["publish", "deploy", "verify"]
    assert manifest.max_parallel_operations == 2
    assert build_ci_execution_argv(manifest, "dev")[:4] == [
        "operation",
        "run",
        "publish,deploy,verify",
        "--config",
    ]
    assert "--max-parallel-operations" not in build_ci_execution_argv(manifest, "dev")


def test_ci_manifest_accepts_single_operation():
    manifest = CiExecutionManifest(
        command="apply-operation",
        operation_names=["deploy"],
        config_ref="platform/stacksmith-config.yaml",
        matrix=[CiExecutionRow(environment="dev", runfile="common/stacksmith.yaml")],
    )

    assert manifest.version == 2
    assert manifest.operation_names == ["deploy"]
    assert build_ci_execution_argv(manifest, "dev")[:4] == [
        "operation",
        "run",
        "deploy",
        "--config",
    ]


@pytest.mark.parametrize("command", ["plan-operation", "apply-operation"])
def test_ci_operation_commands_select_all_when_names_are_omitted(
    command: str,
    tmp_path: Path,
):
    _create_env_files_layout(tmp_path)
    manifest = prepare_ci_execution(
        command=command,
        config_ref=str(_REMOTE_BACKEND_CONFIG),
        gitops_root=str(tmp_path),
        discovery_mode="env-files",
        environments="dev",
        skip_branch_validation=True,
    )

    assert manifest.operation_names == []
    assert build_ci_execution_argv(manifest, "dev")[:3] == [
        "operation",
        "plan" if command == "plan-operation" else "run",
        "--config",
    ]


@pytest.mark.parametrize(
    "operation_names",
    ["deploy,deploy", "deploy,", ",deploy"],
)
def test_prepare_ci_execution_rejects_invalid_operation_batch(
    operation_names: str,
    tmp_path: Path,
):
    _create_env_files_layout(tmp_path)

    with pytest.raises(StacksmithConfigError, match="Operation names"):
        prepare_ci_execution(
            command="apply-operation",
            operation_names=operation_names,
            config_ref=str(_REMOTE_BACKEND_CONFIG),
            gitops_root=str(tmp_path),
            discovery_mode="env-files",
            environments="dev",
            skip_branch_validation=True,
        )


def test_prepare_ci_execution_rejects_local_backend(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    with pytest.raises(
        StacksmithConfigError,
        match="CI prepare rejected environment 'dev'.*the local backend",
    ):
        prepare_ci_execution(
            command="plan",
            config_ref=str(_LOCAL_BACKEND_CONFIG),
            gitops_root=str(tmp_path),
            discovery_mode="env-files",
            environments="dev",
            skip_branch_validation=True,
        )


def test_prepare_ci_test_execution_allows_local_backend_and_feature_branch(
    tmp_path: Path,
):
    _create_env_files_layout(tmp_path)

    manifest = prepare_ci_execution(
        command="test",
        config_ref=str(_LOCAL_BACKEND_CONFIG),
        gitops_root=str(tmp_path),
        discovery_mode="env-files",
        environments="dev",
        event_name="push",
        ref_name="feature/config-tests",
        default_branch="main",
        stacksmith_args_json='["tests.yaml", "--", "-k", "policy"]',
        debug=True,
        no_cas=True,
        offline=True,
        lockfile="stacksmith.lock.yaml",
    )

    argv = build_ci_execution_argv(manifest, "dev")

    assert argv[0] == "test"
    assert argv[argv.index("--config") + 1] == str(_LOCAL_BACKEND_CONFIG)
    assert argv[argv.index("--var") + 1] == "environment=dev"
    assert argv[argv.index("--env-file") + 1] == "/dev/null"
    assert argv[argv.index("--runfile") + 1].endswith("common/stacksmith.yaml")
    assert argv[argv.index("--build-dir") + 1] == ".stacksmith-ci/dev"
    assert argv[-3:] == ["--", "-k", "policy"]
    assert "--debug" in argv
    assert "--no-cas" in argv
    assert "--offline" not in argv
    assert "--lockfile" not in argv


def test_prepare_ci_execution_rejects_dynamic_local_backend(tmp_path: Path):
    _create_env_files_layout(tmp_path)
    config_path = tmp_path / "stacksmith-config.yaml"
    config_path.write_text(
        """
backend:
  inline: |
    def config(**context):
        environment = context["inputs"]["environment"]
        return {
            "type": "local",
            "path": ".state",
        }
default_module_mapping:
  source:
    source: registry
    data:
      address: example/default
      version: "1.0.0"
""",
        encoding="utf-8",
    )

    with pytest.raises(
        StacksmithConfigError,
        match="stack '<unspecified>'.*local backend",
    ):
        prepare_ci_execution(
            command="plan",
            config_ref=str(config_path),
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


def test_ci_config_split_with_complex_urls():
    from stacksmith.ci.service import _ci_config_split

    # Test typical colon-separated paths
    assert _ci_config_split("config.yaml:config_overlay.yaml") == [
        "config.yaml",
        "config_overlay.yaml",
    ]

    # Test HTTP/HTTPS URLs with and without ports
    assert _ci_config_split("https://github.com/org/repo//path.yaml") == [
        "https://github.com/org/repo//path.yaml"
    ]
    assert _ci_config_split("http://localhost:8080/path:config.yaml") == [
        "http://localhost:8080/path",
        "config.yaml",
    ]

    # Test Git SSH URLs (with port/host-path colon)
    assert _ci_config_split("git+ssh://git@github.com:org/repo.git//path.yaml") == [
        "git+ssh://git@github.com:org/repo.git//path.yaml"
    ]
    assert _ci_config_split("git@github.com:org/repo.git//path.yaml") == [
        "git@github.com:org/repo.git//path.yaml"
    ]

    # Test complex mixed references
    combined = "https://github.com/org/repo//path.yaml:git@github.com:org/repo.git//path.yaml:config.yaml"
    assert _ci_config_split(combined) == [
        "https://github.com/org/repo//path.yaml",
        "git@github.com:org/repo.git//path.yaml",
        "config.yaml",
    ]

    # Test single-letter parameter (should NOT be treated as Windows drive letter unless it looks like a path)
    assert _ci_config_split("a:config.yaml") == ["a", "config.yaml"]
    assert _ci_config_split("C:\\path\\config.yaml:other.yaml") == [
        "C:\\path\\config.yaml",
        "other.yaml",
    ]
    assert _ci_config_split("C:/path/config.yaml:other.yaml") == [
        "C:/path/config.yaml",
        "other.yaml",
    ]


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


def test_prepare_ci_destroy_execution_rejects_offline_without_locked(
    tmp_path: Path,
):
    _create_env_files_layout(tmp_path)

    with pytest.raises(ValueError, match="requires locked"):
        prepare_ci_execution(
            command="destroy",
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


def test_prepare_ci_manifest_from_env_reads_operation_batch(
    monkeypatch, tmp_path: Path
):
    _create_env_files_layout(tmp_path)
    monkeypatch.setenv("INPUT_COMMAND", "apply-operation")
    monkeypatch.setenv("INPUT_OPERATION_NAMES", "publish, deploy")
    monkeypatch.setenv("STACKSMITH_MAX_PARALLEL_OPERATIONS", "4")
    monkeypatch.setenv("INPUT_CONFIG_REF", str(_REMOTE_BACKEND_CONFIG))
    monkeypatch.setenv("INPUT_GITOPS_ROOT", str(tmp_path))
    monkeypatch.setenv("INPUT_DISCOVERY_MODE", "env-files")
    monkeypatch.setenv("SKIP_BRANCH_VALIDATION", "true")

    manifest = prepare_ci_manifest_from_env()

    assert manifest.version == 2
    assert manifest.operation_names == ["publish", "deploy"]
    assert manifest.max_parallel_operations == 4


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


def test_prepare_ci_execution_rejects_destroy_on_pull_request(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    with pytest.raises(StacksmithConfigError, match="not allowed on pull requests"):
        prepare_ci_execution(
            command="destroy",
            config_ref="platform/stacksmith-config.yaml",
            gitops_root=str(tmp_path),
            discovery_mode="env-files",
            event_name="pull_request",
            base_ref="main",
            default_branch="main",
        )


def test_prepare_ci_execution_accepts_destroy_on_default_branch(tmp_path: Path):
    _create_env_files_layout(tmp_path)

    manifest = prepare_ci_execution(
        command="destroy",
        config_ref=str(_REMOTE_BACKEND_CONFIG),
        gitops_root=str(tmp_path),
        discovery_mode="env-files",
        environments="dev",
        event_name="push",
        ref_name="main",
        default_branch="main",
    )

    assert manifest.command == "destroy"
    assert [row.environment for row in manifest.matrix] == ["dev"]


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
    jenkins_pipeline = (repository_root / "jenkins/vars/stacksmith.groovy").read_text()
    apply_workflow = (
        repository_root / "examples/github-actions/stacksmith-apply.yml"
    ).read_text()

    assert "stacksmith ci prepare-from-env" in actions_workflow
    assert "stacksmith ci execute-from-env" in actions_executor
    assert "stacksmith ci prepare-from-env" in jenkins_pipeline
    assert "stacksmith ci execute-from-env" in jenkins_pipeline
    assert not (
        repository_root / "jenkins/vars/withFolderPropertiesIfAvailable.groovy"
    ).exists()
    assert "StepDescriptor.byFunctionName('withFolderProperties')" in jenkins_pipeline
    assert ".metaClass" not in jenkins_pipeline
    assert "INPUT_DEBUG" in actions_workflow
    assert "INPUT_DEBUG" in jenkins_pipeline
    assert (
        "STACKSMITH_JENKINS_USERNAME: ${{ secrets.STACKSMITH_JENKINS_USERNAME }}"
        in actions_executor
    )
    assert (
        "STACKSMITH_JENKINS_API_TOKEN: ${{ secrets.STACKSMITH_JENKINS_API_TOKEN }}"
        in actions_executor
    )
    assert "operation_names_json" not in actions_workflow
    assert "INPUT_OPERATION_NAME:" not in actions_workflow
    assert "OPERATION_NAMES_JSON" not in jenkins_pipeline
    assert "string(name: 'OPERATION_NAME'" not in jenkins_pipeline
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
    assert "string(name: 'MAX_PARALLEL_OPERATIONS'" not in jenkins_pipeline
    assert "params.MAX_PARALLEL_OPERATIONS" not in jenkins_pipeline
    assert "STACKSMITH_MAX_PARALLEL_OPERATIONS" in jenkins_pipeline
    plan_stage = jenkins_pipeline.index("stage('Plan')")
    operation_plan_stage = jenkins_pipeline.index("stage('Plan operation(s)')")
    approval_stage = jenkins_pipeline.index("stage('Approve')")
    apply_stage = jenkins_pipeline.index("stage('Apply')")
    operation_stage = jenkins_pipeline.index("stage('Run operation(s)')")
    destroy_operation_stage = jenkins_pipeline.index("stage('Destroy operation state')")
    destroy_stage = jenkins_pipeline.index("stage('Destroy')")
    assert plan_stage < operation_plan_stage < approval_stage < apply_stage
    assert approval_stage < operation_stage < destroy_operation_stage < destroy_stage
    assert "setManifestCommand" not in jenkins_pipeline
    assert jenkins_pipeline.count("returnPojo: true") == 4
    assert "readJSON(text: manifestOutput, returnPojo: true)" in jenkins_pipeline
    assert "import org.jenkinsci.plugins.pipeline.modeldefinition.Utils" in (
        jenkins_pipeline
    )
    assert jenkins_pipeline.count("Utils.markStageSkippedForConditional") == 7
    push_trigger = apply_workflow.split("  push:", 1)[1].split(
        "  workflow_dispatch:", 1
    )[0]
    assert "paths:" not in push_trigger
    assert "branches:" not in push_trigger
    assert "github.event.repository.default_branch" in apply_workflow
    assert '"STACKSMITH_CI_PHASE=${command}"' in jenkins_pipeline
    assert '--phase "$STACKSMITH_CI_PHASE"' in jenkins_pipeline
    assert (
        "inputs.command == 'plan' || inputs.command == 'apply' || "
        "inputs.command == 'destroy'"
    ) in actions_workflow
    assert "needs: [discover, plan, plan-operation]" in actions_workflow
    assert "needs: [discover, plan-operation]" in actions_workflow
    assert "needs: [discover, plan-operation, apply]" in actions_workflow
    assert (
        "needs: [discover, plan, plan-operation, destroy-operation]" in actions_workflow
    )
    assert "phase: plan" in actions_workflow
    assert "phase: apply" in actions_workflow
    assert "phase: destroy" in actions_workflow
    assert "phase: plan-operation" in actions_workflow
    assert "run-after-apply-operation:" in actions_workflow
    assert "destroy-operation:" in actions_workflow
    assert "inputs.command == 'plan-operation'" in actions_workflow
    assert (
        "env.COMMAND in ['plan', 'apply', 'destroy', 'plan-operation', "
        "'apply-operation']" in jenkins_pipeline
    )
    assert "      max_parallel_operations:" not in actions_workflow
    assert "STACKSMITH_MAX_PARALLEL_OPERATIONS" in actions_workflow
    assert "inputs.phase || fromJson(inputs.ci_manifest).command" in actions_executor
    assert "== 'destroy'" in actions_executor
    assert "STACKSMITH_PLAN_ARTIFACT_KIND" in actions_executor
    assert "destroy-plan.json" in actions_executor
    assert "complete isolated operation state" in actions_executor
    assert "destroy-plan.json" in jenkins_pipeline
    assert '--phase "$STACKSMITH_CI_PHASE"' in actions_executor


def test_jenkins_pipeline_uses_environment_controlled_test_mode():
    jenkins_pipeline = (
        Path(__file__).parents[1] / "jenkins/vars/stacksmith.groovy"
    ).read_text()

    assert "'apply-operation', 'test']" not in jenkins_pipeline
    assert "parseBoolean(env.STACKSMITH_TEST_PIPELINE)" in jenkins_pipeline
    assert "parameters(buildPipelineParameters(testPipeline))" in jenkins_pipeline
    assert "if (testPipeline) {\n        return sharedParameters" in jenkins_pipeline
    assert "env.COMMAND = testPipeline ? 'test'" in jenkins_pipeline
    assert "if (testPipeline)" in jenkins_pipeline
    assert "stage('Test')" in jenkins_pipeline
    assert "'test'," in jenkins_pipeline
    assert "stacksmith test" not in jenkins_pipeline
    assert "poe test" not in jenkins_pipeline


def test_jenkins_pipeline_orders_apply_and_destroy_operation_phases():
    jenkins_pipeline = (
        Path(__file__).parents[1] / "jenkins/vars/stacksmith.groovy"
    ).read_text()
    plan_stage = jenkins_pipeline.split("stage('Plan')", 1)[1].split(
        "stage('Plan operation(s)')", 1
    )[0]
    plan_operation_stage = jenkins_pipeline.split("stage('Plan operation(s)')", 1)[
        1
    ].split("stage('Approve')", 1)[0]
    approval_stage = jenkins_pipeline.split("stage('Approve')", 1)[1].split(
        "stage('Apply')", 1
    )[0]
    apply_stage = jenkins_pipeline.split("stage('Apply')", 1)[1].split(
        "stage('Run operation(s)')", 1
    )[0]
    run_operation_stage = jenkins_pipeline.split("stage('Run operation(s)')", 1)[
        1
    ].split("stage('Destroy operation state')", 1)[0]
    destroy_operation_stage = jenkins_pipeline.split(
        "stage('Destroy operation state')", 1
    )[1].split("stage('Destroy')", 1)[0]
    destroy_stage = jenkins_pipeline.split("stage('Destroy')", 1)[1]

    assert "env.COMMAND in ['plan', 'apply', 'destroy']" in plan_stage
    assert "'destroy', 'plan-operation'" in plan_operation_stage
    assert "env.COMMAND in ['apply', 'destroy', 'apply-operation']" in approval_stage
    assert "buildApprovalMessage(" in approval_stage
    assert "operation state and infrastructure" in jenkins_pipeline
    assert "env.COMMAND == 'apply'" in apply_stage
    assert "env.COMMAND in ['apply', 'apply-operation']" in run_operation_stage
    assert "env.COMMAND == 'destroy'" in destroy_operation_stage
    assert "'operation'," in destroy_operation_stage
    assert "env.COMMAND == 'destroy'" in destroy_stage
    assert "'destroy'," in destroy_stage


def test_ci_workflow_adapters_preserve_distinct_destroy_plan_artifacts():
    repository_root = Path(__file__).parents[1]
    actions_executor = (
        repository_root / ".github/workflows/stacksmith-gitops-reusable.yml"
    ).read_text()
    jenkins_pipeline = (repository_root / "jenkins/vars/stacksmith.groovy").read_text()

    assert (
        "fromJson(inputs.ci_manifest).command == 'destroy' && 'destroy-plan' || "
        "'plan'" in actions_executor
    )
    assert (
        "name: stacksmith-${{ env.STACKSMITH_PLAN_ARTIFACT_KIND }}-"
        "${{ inputs.environment }}-${{ github.sha }}" in actions_executor
    )
    assert "${{ env.STACKSMITH_PLAN_ARTIFACT_KIND }}.json" in actions_executor
    assert "(success() || failure()) && inputs.upload_artifacts" in actions_executor
    assert 'heading = "Destroy Preview"' in actions_executor
    assert "- Plan artifact: " in actions_executor

    assert (
        "env.COMMAND == 'destroy' ? 'destroy-plan.json' : 'plan.json'"
        in jenkins_pipeline
    )
    status = jenkins_pipeline.index("int status = withStacksmithCredentials(")
    archive = jenkins_pipeline.index("archiveArtifacts(", status)
    branch_result = jenkins_pipeline.index("return status", archive)
    assert status < archive < branch_result


def test_github_destroy_example_is_manual_only_and_environment_scoped():
    destroy_workflow = (
        Path(__file__).parents[1] / "examples/github-actions/stacksmith-destroy.yml"
    ).read_text()
    plan_workflow = (
        Path(__file__).parents[1] / "examples/github-actions/stacksmith-plan.yml"
    ).read_text()
    triggers = destroy_workflow.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    environment_input = triggers.split("      environments:\n", 1)[1].split(
        "      gitops_root:\n", 1
    )[0]

    assert "  workflow_dispatch:" in triggers
    assert "  push:" not in triggers
    assert "  pull_request:" not in triggers
    assert "required: true" in environment_input
    assert "command: destroy" in destroy_workflow
    assert "environments: ${{ inputs.environments }}" in destroy_workflow
    assert "secrets: inherit" in destroy_workflow
    assert ".github/workflows/stacksmith-destroy.yml" in plan_workflow


def test_github_operation_example_uses_the_manifest_command_name():
    operation_workflow = (
        Path(__file__).parents[1] / "examples/github-actions/stacksmith-operation.yml"
    ).read_text()

    assert "command: apply-operation" in operation_workflow
    assert "command: operation" not in operation_workflow


def test_github_pipeline_orders_apply_and_destroy_operation_phases():
    actions_workflow = (
        Path(__file__).parents[1]
        / ".github/workflows/stacksmith-gitops-opinionated-reusable.yml"
    ).read_text()
    apply_job = actions_workflow.split("\n  apply:\n", 1)[1].split(
        "\n  plan-operation:\n", 1
    )[0]
    run_operation_job = actions_workflow.split("\n  run-operation:\n", 1)[1].split(
        "\n  run-after-apply-operation:\n", 1
    )[0]
    after_apply_job = actions_workflow.split("\n  run-after-apply-operation:\n", 1)[
        1
    ].split("\n  destroy-operation:\n", 1)[0]
    destroy_operation_job = actions_workflow.split("\n  destroy-operation:\n", 1)[
        1
    ].split("\n  destroy:\n", 1)[0]
    destroy_job = actions_workflow.split("\n  destroy:\n", 1)[1].split(
        "\n  no-op:\n", 1
    )[0]

    assert "needs: [discover, plan, plan-operation]" in apply_job
    assert "inputs.command == 'apply-operation'" in run_operation_job
    assert "inputs.command == 'apply'" not in run_operation_job
    assert "needs: [discover, plan-operation, apply]" in after_apply_job
    assert "inputs.command == 'apply'" in after_apply_job
    assert "needs: [discover, plan, plan-operation]" in destroy_operation_job
    assert "phase: operation" in destroy_operation_job
    assert "needs: [discover, plan, plan-operation, destroy-operation]" in destroy_job
    assert "phase: destroy" in destroy_job


def test_jenkins_pipeline_provides_configured_credentials_during_preparation():
    jenkins_pipeline = (
        Path(__file__).parents[1] / "jenkins/vars/stacksmith.groovy"
    ).read_text()

    credential_scope = jenkins_pipeline.index(
        "withStacksmithCredentials(\n"
        "                        env.STACKSMITH_CREDENTIALS_JSON ?: '',\n"
        "                        'manifest preparation'"
    )
    prepare_command = jenkins_pipeline.index(
        "stacksmith ci prepare-from-env", credential_scope
    )

    assert credential_scope < prepare_command
    assert jenkins_pipeline.count("withStacksmithCredentials(") == 3


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


def test_ci_destroy_plan_uses_a_distinct_redacted_artifact_path():
    argv = build_ci_execution_argv(
        CiExecutionManifest(
            command="destroy",
            config_ref="platform/stacksmith-config.yaml",
            matrix=[
                CiExecutionRow(
                    environment="dev",
                    runfile="common/stacksmith.yaml",
                )
            ],
        ),
        "dev",
        "plan",
    )

    assert "--save-redacted-plan-json" in argv
    assert "--save-plan-json" not in argv
    assert argv[argv.index("--save-redacted-plan-json") + 1] == (
        ".stacksmith-ci/dev/destroy-plan.json"
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


def test_ci_destroy_manifest_supports_destroy_plan_then_destroy_phases():
    manifest = CiExecutionManifest(
        command="destroy",
        config_ref="platform/stacksmith-config.yaml",
        locked=True,
        offline=True,
        lockfile="locks/stacksmith.lock.yaml",
        matrix=[CiExecutionRow(environment="dev", runfile="common/stacksmith.yaml")],
    )

    plan_argv = build_ci_execution_argv(manifest, "dev", "plan")
    destroy_argv = build_ci_execution_argv(manifest, "dev", "destroy")

    assert plan_argv[0] == "plan"
    assert "--destroy" in plan_argv
    assert "--save-redacted-plan-json" in plan_argv
    assert destroy_argv[0] == "destroy"
    assert "--auto-approve" in destroy_argv
    assert "--locked" in destroy_argv
    assert "--offline" in destroy_argv
    assert destroy_argv[destroy_argv.index("--lockfile") + 1] == (
        "locks/stacksmith.lock.yaml"
    )


def test_ci_destroy_manifest_supports_operation_state_cleanup_phases():
    manifest = CiExecutionManifest(
        command="destroy",
        config_ref="platform/stacksmith-config.yaml",
        matrix=[CiExecutionRow(environment="dev", runfile="common/stacksmith.yaml")],
    )

    plan_argv = build_ci_execution_argv(manifest, "dev", "plan-operation")
    destroy_argv = build_ci_execution_argv(manifest, "dev", "operation")

    assert plan_argv[:2] == ["operation", "plan"]
    assert "--destroy" in plan_argv
    assert "--after-apply" not in plan_argv
    assert destroy_argv[:2] == ["operation", "destroy"]
    assert "--auto-approve" in destroy_argv


def test_ci_destroy_manifest_rejects_force_rerun():
    with pytest.raises(ValueError, match="cannot force operation reruns"):
        CiExecutionManifest(
            command="destroy",
            config_ref="platform/stacksmith-config.yaml",
            force_rerun=True,
        )


def test_ci_operation_manifest_supports_plan_then_run_phases():
    manifest = CiExecutionManifest(
        command="apply-operation",
        operation_names=["deploy"],
        config_ref="platform/stacksmith-config.yaml",
        force_rerun=True,
        matrix=[CiExecutionRow(environment="dev", runfile="common/stacksmith.yaml")],
    )

    plan_argv = build_ci_execution_argv(manifest, "dev", "plan-operation")
    run_argv = build_ci_execution_argv(manifest, "dev", "operation")

    assert plan_argv[:3] == ["operation", "plan", "deploy"]
    assert run_argv[:3] == ["operation", "run", "deploy"]
    assert "--force-rerun" in plan_argv
    assert "--force-rerun" in run_argv
    assert "--max-parallel-operations" not in plan_argv
    assert "--max-parallel-operations" not in run_argv


def test_ci_plan_manifest_supports_after_apply_operation_plan_phase():
    manifest = CiExecutionManifest(
        command="plan",
        config_ref="platform/stacksmith-config.yaml",
        matrix=[CiExecutionRow(environment="dev", runfile="common/stacksmith.yaml")],
    )

    operation_plan_argv = build_ci_execution_argv(
        manifest,
        "dev",
        "plan-operation",
    )

    assert operation_plan_argv[:3] == ["operation", "plan", "--config"]
    assert "--after-apply" in operation_plan_argv


def test_ci_apply_manifest_supports_after_apply_operation_plan_phase():
    manifest = CiExecutionManifest(
        command="apply",
        config_ref="platform/stacksmith-config.yaml",
        matrix=[CiExecutionRow(environment="dev", runfile="common/stacksmith.yaml")],
    )

    operation_plan_argv = build_ci_execution_argv(
        manifest,
        "dev",
        "plan-operation",
    )

    assert operation_plan_argv[:3] == ["operation", "plan", "--config"]
    assert "--after-apply" in operation_plan_argv


def test_ci_apply_manifest_uses_no_after_apply_in_apply_phase():
    manifest = CiExecutionManifest(
        command="apply",
        config_ref="platform/stacksmith-config.yaml",
        matrix=[CiExecutionRow(environment="dev", runfile="common/stacksmith.yaml")],
    )

    apply_argv = build_ci_execution_argv(manifest, "dev", "apply")

    assert apply_argv[0] == "apply"
    assert "--auto-approve" in apply_argv
    assert "--no-after-apply" in apply_argv


def test_ci_manifest_rejects_unapproved_execution_phase():
    manifest = CiExecutionManifest(
        command="plan",
        config_ref="platform/stacksmith-config.yaml",
        matrix=[CiExecutionRow(environment="dev", runfile="common/stacksmith.yaml")],
    )

    with pytest.raises(StacksmithError, match="cannot execute phase 'apply'"):
        build_ci_execution_argv(manifest, "dev", "apply")

    with pytest.raises(StacksmithError, match="cannot execute phase 'test'"):
        build_ci_execution_argv(manifest, "dev", "test")

    with pytest.raises(StacksmithError, match="cannot execute phase 'destroy'"):
        build_ci_execution_argv(manifest, "dev", "destroy")


def test_prepare_ci_execution_accepts_colon_delimited_config_refs(tmp_path: Path):
    _create_env_files_layout(tmp_path)
    base_config = tmp_path / "base" / "stacksmith-config.yaml"
    base_config.parent.mkdir()
    base_config.write_text(
        "backend:\n  data:\n    type: remote\nmodule_mappings: {}\n"
        "default_module_mapping:\n  source:\n"
        "    source: local\n    data:\n      path: ./modules\n"
    )
    overlay_config = tmp_path / "overlay" / "stacksmith-config.yaml"
    overlay_config.parent.mkdir()
    overlay_config.write_text("description: overlay\n")

    manifest = prepare_ci_execution(
        command="plan",
        config_ref=f"{base_config}:{overlay_config}",
        gitops_root=str(tmp_path),
        discovery_mode="env-files",
        environments="dev",
        skip_branch_validation=True,
    )

    assert manifest.config_ref == f"{base_config}:{overlay_config}"
    argv = build_ci_execution_argv(manifest, "dev")
    assert "--config" in argv
