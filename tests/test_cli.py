import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import stacksmith.cli.main
from stacksmith import api
from stacksmith.cli import args as cli_args
from stacksmith.cli import main as cli_main
from stacksmith.exceptions import StacksmithConfigError
from stacksmith.inspector import ComponentTypeInfo, InputInfo
from stacksmith.models import MergePolicy, RunFile, render_file_reference


@pytest.fixture
def parser():
    return stacksmith.cli.main._build_parser()


def _capture_run_all_stacks_call(
    monkeypatch: pytest.MonkeyPatch,
    return_code: int = 0,
) -> dict[str, object]:
    calls: dict[str, object] = {}

    def _fake_run_all_stacks(action, root, **kwargs):
        calls["run"] = (action, root, kwargs)
        return return_code

    monkeypatch.setattr(cli_main, "run_all_stacks", _fake_run_all_stacks)
    return calls


def _capture_run_stack_action_call(
    monkeypatch: pytest.MonkeyPatch,
    return_code: int = 0,
) -> dict[str, object]:
    calls: dict[str, object] = {}

    def _fake_run_stack_action(action, stack_file, **kwargs):
        calls["run"] = (action, stack_file, kwargs)
        return return_code

    monkeypatch.setattr(cli_main, "run_stack_action", _fake_run_stack_action)
    return calls


def _capture_run_stack_operation_call(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    calls: dict[str, object] = {}

    def _fake_run_stack_operation(stack_file, operation_name, **kwargs):
        calls["run"] = (stack_file, operation_name, kwargs)
        return {"operation": operation_name, "exit_code": 0}

    monkeypatch.setattr(cli_main, "run_stack_operation", _fake_run_stack_operation)
    return calls


def _diagnostics_payload(
    remote_cache_entries: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "stack_file": "stack.yaml",
        "config_paths": ["stacksmith-config.yaml"],
        "build_directory": ".stacksmith",
        "remote_cache_directory": ".stacksmith/.cache",
        "remote_cache_exists": True,
        "remote_cache_entries": remote_cache_entries or [],
        "vendor_directory": ".stacksmith/vendor",
        "vendor_directory_exists": True,
        "vendor_manifest_path": None,
        "vendored_modules": [],
        "vendor_directories": [],
    }


def test_load_env_file_sets_variables(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "STACKSMITH_CONFIG=/tmp/config.yaml\nCI=true\nSTACKSMITH_VAR_FOO=bar\n# comment line\n"
    )
    monkeypatch.delenv("STACKSMITH_CONFIG", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("STACKSMITH_VAR_FOO", raising=False)

    cli_main.load_env_files([env_path])

    assert os.environ["STACKSMITH_CONFIG"] == "/tmp/config.yaml"
    assert os.environ["CI"] == "true"
    assert os.environ["STACKSMITH_VAR_FOO"] == "bar"


def test_config_default_from_env(monkeypatch, parser):
    monkeypatch.setenv("STACKSMITH_CONFIG", "/tmp/config.yaml")
    args = parser.parse_args(["validate", "stack.yaml"])

    assert api._resolve_config_paths(args.config) == [Path("/tmp/config.yaml")]


def test_root_defaults_to_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(["run-all", "plan", "--config", "/tmp/config.yaml"])

    assert args.root == tmp_path
    assert args.clean is False
    assert args.config == ["/tmp/config.yaml"]


def test_info_inspect_has_basic_flag(parser):
    args = parser.parse_args(["info", "inspect", "aws_s3_bucket", "--basic"])

    assert args.command == "info"
    assert args.info_command == "inspect"
    assert args.component_type == ["aws_s3_bucket"]
    assert args.basic is True


def test_info_diagnose_has_stack_file(parser):
    args = parser.parse_args(["info", "diagnose", "stack.yaml"])

    assert args.command == "info"
    assert args.info_command == "diagnose"
    assert args.stack_file == Path("stack.yaml")
    assert args.format == "table"


def test_info_environments_has_gitops_flags(parser):
    args = parser.parse_args(
        [
            "info",
            "environments",
            "--gitops-root",
            "./examples/gitops-repo",
            "--discovery-mode",
            "env-files",
            "--event-name",
            "push",
            "--changed-path",
            "examples/gitops-repo/common/stacksmith.yaml",
            "--format",
            "json",
        ]
    )

    assert args.command == "info"
    assert args.info_command == "environments"
    assert args.gitops_root == "./examples/gitops-repo"
    assert args.discovery_mode == "env-files"
    assert args.event_name == "push"
    assert args.changed_path == ["examples/gitops-repo/common/stacksmith.yaml"]
    assert args.format == "json"


def test_info_environments_defaults_to_auto_discovery_mode(parser):
    args = parser.parse_args(["info", "environments"])

    assert args.discovery_mode == "auto"


def test_ci_validate_has_workflow_flags(parser):
    args = parser.parse_args(
        [
            "ci",
            "validate",
            "--gitops-root",
            "examples/gitops-repo",
            "--discovery-mode",
            "folders",
            "--workflow-runfile",
            "common/stacksmith.yaml",
            "--workflow-env-file",
            "/dev/null",
            "--workflow-validation-report-format",
            "json",
        ]
    )

    assert args.command == "ci"
    assert args.ci_command == "validate"
    assert args.workflow_runfile == "common/stacksmith.yaml"
    assert args.workflow_env_file == "/dev/null"
    assert args.workflow_validation_report_format == "json"
    assert args.format == "json"


def test_run_all_clean_flag(monkeypatch, tmp_path, parser):
    monkeypatch.chdir(tmp_path)
    args = parser.parse_args(
        ["run-all", "plan", "--clean", "--config", "/tmp/config.yaml"]
    )

    assert args.clean is True


def test_env_file_integration_sets_defaults(tmp_path, monkeypatch, parser):
    env_path = tmp_path / ".env"
    env_path.write_text("STACKSMITH_CONFIG=/tmp/config.yaml\nCI=true\n")
    monkeypatch.delenv("STACKSMITH_CONFIG", raising=False)
    env_files = cli_args.get_env_file_paths(
        ["validate", "stack.yaml", "--env-file", str(env_path)]
    )

    assert env_files == [env_path]

    cli_main.load_env_files([env_path])
    args = parser.parse_args(["validate", "stack.yaml"])

    assert api._resolve_config_paths(args.config) == [Path("/tmp/config.yaml")]


def test_default_dotenv_file_is_detected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text("STACKSMITH_CONFIG=/tmp/config.yaml\n")

    assert cli_args.get_env_file_paths(["validate", "stack.yaml"]) == [env_path]


def test_default_run_file_is_detected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runfile = tmp_path / "stacksmith.yaml"
    runfile.write_text("stacks:\n  - ./stack.yaml\n", encoding="utf-8")

    assert cli_args.get_default_run_file() == str(runfile)


def test_stack_file_default_from_env_var(monkeypatch):
    monkeypatch.setenv("STACKSMITH_STACK", "/tmp/other-stack.yaml")
    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(["validate"])

    assert args.stack_file == Path("/tmp/other-stack.yaml")


def test_env_files_are_repeatable(monkeypatch, tmp_path, parser):
    monkeypatch.setenv("HOME", str(tmp_path))
    args = parser.parse_args(
        [
            "validate",
            "stack.yaml",
            "--env-file",
            "~/.env.base",
            "--env-file",
            "~/.env.override",
        ]
    )

    assert args.env_file == [tmp_path / ".env.base", tmp_path / ".env.override"]


def test_path_arguments_expand_user_home(monkeypatch, tmp_path, parser):
    monkeypatch.setenv("HOME", str(tmp_path))
    env_path = tmp_path / ".env"
    env_path.write_text("STACKSMITH_CONFIG=/tmp/config.yaml\n")

    args = parser.parse_args(
        [
            "validate",
            "stack.yaml",
            "--env-file",
            "~/.env",
            "--build-dir",
            "~/build",
        ]
    )

    assert args.env_file == [tmp_path / ".env"]
    assert args.build_dir == tmp_path / "build"


def test_config_env_supports_multiple_paths(monkeypatch, parser):
    monkeypatch.setenv(
        "STACKSMITH_CONFIG", f"/tmp/base.yaml{os.pathsep}/tmp/override.yaml"
    )
    args = parser.parse_args(["validate", "stack.yaml"])

    assert api._resolve_config_paths(args.config) == [
        Path("/tmp/base.yaml"),
        Path("/tmp/override.yaml"),
    ]


def test_config_env_supports_colon_delimited_quoted_urls(monkeypatch):
    monkeypatch.setenv(
        "STACKSMITH_CONFIG",
        '"https://example.com/base.yaml":"git+https://github.com/org/config.git//override.yaml@v1"',
    )
    assert api._default_config_paths() == [
        "https://example.com/base.yaml",
        "git+https://github.com/org/config.git//override.yaml@v1",
    ]


def test_config_is_repeatable(monkeypatch, parser):
    monkeypatch.delenv("STACKSMITH_CONFIG", raising=False)
    args = parser.parse_args(
        [
            "validate",
            "stack.yaml",
            "--config",
            "/tmp/base.yaml",
            "--config",
            "/tmp/override.yaml",
        ]
    )

    assert args.config == ["/tmp/base.yaml", "/tmp/override.yaml"]


def test_stack_flag_is_repeatable(parser):
    args = parser.parse_args(
        [
            "validate",
            "--stack",
            "./base-stack.yaml",
            "--stack",
            "./override-stack.yaml",
        ]
    )

    assert args.stack == ["./base-stack.yaml", "./override-stack.yaml"]


def test_merge_mode_flag_is_supported(parser):
    args = parser.parse_args(["validate", "--merge-mode", "override"])

    assert args.merge_mode == "override"


def test_plan_subcommand_supports_destroy_flag(parser):
    args = parser.parse_args(["plan", "stack.yaml", "--destroy"])

    assert args.destroy is True
    assert args.command == "plan"


def test_plan_subcommand_supports_tag_expression(parser):
    args = parser.parse_args(
        [
            "plan",
            "stack.yaml",
            "--tag",
            "prod",
            "--tag",
            "web",
            "--tag-expr",
            "contains(tags, 'prod')",
        ]
    )

    assert args.tag == ["prod", "web"]
    assert args.tag_expr == "contains(tags, 'prod')"


def test_plan_subcommand_supports_strict_validation_warnings(parser):
    args = parser.parse_args(["plan", "stack.yaml", "--strict-validation-warnings"])

    assert args.strict_validation_warnings is True


def test_validate_subcommand_supports_validation_report_format(parser):
    args = parser.parse_args(
        ["validate", "stack.yaml", "--validation-report-format", "json"]
    )

    assert args.validation_report_format == "json"


def test_plan_subcommand_supports_validation_report_format(parser):
    args = parser.parse_args(
        ["plan", "stack.yaml", "--validation-report-format", "json"]
    )

    assert args.validation_report_format == "json"


def test_plan_subcommand_supports_debug_and_save_plan_json(tmp_path):
    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(
        [
            "plan",
            "stack.yaml",
            "--debug",
            "--save-plan-json",
            str(tmp_path / "plan.json"),
        ]
    )

    assert args.debug is True
    assert args.save_plan_json == tmp_path / "plan.json"


def test_plan_subcommand_supports_fail_on_changes(parser):
    args = parser.parse_args(["plan", "stack.yaml", "--fail-on-changes"])

    assert args.fail_on_changes is True


def test_plan_subcommand_supports_quiet_and_save_plan_json(tmp_path):
    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(
        [
            "plan",
            "stack.yaml",
            "--quiet",
            "--save-plan-json",
            str(tmp_path / "plan.json"),
        ]
    )

    assert args.quiet is True
    assert args.debug is False
    assert args.save_plan_json == tmp_path / "plan.json"


def test_run_all_subcommand_supports_plan_destroy(parser):
    args = parser.parse_args(["run-all", "plan", "--destroy"])

    assert args.action == "plan"
    assert args.destroy is True


def test_run_all_subcommand_supports_debug_and_save_plan_json(tmp_path, parser):
    args = parser.parse_args(
        [
            "run-all",
            "plan",
            "--debug",
            "--save-plan-json",
            str(tmp_path / "plans"),
        ]
    )

    assert args.debug is True
    assert args.save_plan_json == tmp_path / "plans"


def test_run_all_subcommand_supports_fail_on_changes(parser):
    args = parser.parse_args(["run-all", "plan", "--fail-on-changes"])

    assert args.fail_on_changes is True


def test_run_all_subcommand_supports_quiet_and_save_plan_json(tmp_path, parser):
    args = parser.parse_args(
        [
            "run-all",
            "plan",
            "--quiet",
            "--save-plan-json",
            str(tmp_path / "plans"),
        ]
    )

    assert args.quiet is True
    assert args.debug is False
    assert args.save_plan_json == tmp_path / "plans"


def test_debug_and_quiet_are_mutually_exclusive(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(["plan", "stack.yaml", "--debug", "--quiet"])


def test_is_quiet_enabled_reads_namespace_flag():
    assert cli_args.is_quiet_enabled(SimpleNamespace(quiet=True)) is True
    assert cli_args.is_quiet_enabled(SimpleNamespace(quiet=False)) is False
    assert cli_args.is_quiet_enabled(None) is False


def test_parse_var_args_raises_stacksmith_config_error_on_invalid_format():
    with pytest.raises(StacksmithConfigError, match="Invalid --var format"):
        cli_args.parse_var_args(["missing_equals"])


def test_parse_input_layers_raises_stacksmith_config_error_on_invalid_var_layer():
    with pytest.raises(StacksmithConfigError, match="Invalid --var format"):
        cli_args.parse_input_layers([("var", "missing_equals")])


def test_validate_help_lists_stacksmith_log_categories(parser, capsys):
    with pytest.raises(SystemExit):
        parser.parse_args(["validate", "--help"])

    captured = capsys.readouterr()
    assert "stacksmith.api" in captured.out
    assert "stacksmith.runner" in captured.out


def test_configure_logging_quiet_sets_error_root_level(monkeypatch):
    added_levels: list[str] = []

    monkeypatch.setattr(cli_main.LOGGER, "remove", lambda *args, **kwargs: None)

    def _fake_add(*args, **kwargs):
        added_levels.append(kwargs["level"])
        return 1

    monkeypatch.setattr(cli_main.LOGGER, "add", _fake_add)

    cli_main._configure_logging(debug=True, quiet=True)

    assert added_levels[0] == "ERROR"


def test_run_all_subcommand_supports_tag_selectors(parser):
    args = parser.parse_args(
        [
            "run-all",
            "plan",
            "--tag",
            "prod",
            "--tag-expr",
            "contains(tags, 'prod')",
        ]
    )

    assert args.tag == ["prod"]
    assert args.tag_expr == "contains(tags, 'prod')"


def test_run_all_subcommand_supports_strict_validation_warnings(parser):
    args = parser.parse_args(["run-all", "plan", "--strict-validation-warnings"])

    assert args.strict_validation_warnings is True


def test_run_all_subcommand_supports_validation_report_format(parser):
    args = parser.parse_args(["run-all", "plan", "--validation-report-format", "json"])

    assert args.validation_report_format == "json"


def test_run_all_subcommand_supports_label_filters(parser):
    args = parser.parse_args(
        [
            "run-all",
            "plan",
            "--include-tag",
            "prod",
            "--include-tag",
            "backend",
            "--exclude-tag",
            "experimental",
        ]
    )

    assert args.include_tag == ["prod", "backend"]
    assert args.exclude_tag == ["experimental"]


def test_cmd_run_all_passes_label_filters(monkeypatch, parser):
    calls = _capture_run_all_stacks_call(monkeypatch)

    args = parser.parse_args(
        [
            "run-all",
            "plan",
            "--include-tag",
            "prod",
            "--exclude-tag",
            "experimental",
        ]
    )

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 0
    assert calls["run"][2]["include_tags"] == ["prod"]
    assert calls["run"][2]["exclude_tags"] == ["experimental"]


def test_cmd_run_all_uses_runner(monkeypatch, parser):
    calls = _capture_run_all_stacks_call(monkeypatch)

    args = parser.parse_args(["run-all", "plan", "--destroy"])

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 0
    assert calls["run"][0] == "plan"
    assert calls["run"][1] == args.root
    assert calls["run"][2]["destroy"] is True


@pytest.mark.parametrize(
    ("option_args", "keyword", "expected"),
    [
        (
            ["--tag-expr", "contains(tags, 'prod')"],
            "tag_expr",
            "contains(tags, 'prod')",
        ),
        (["--save-plan-json", "plans"], "save_plan_json", Path("plans")),
        (["--fail-on-changes"], "fail_on_changes", True),
        (["--validation-report-format", "json"], "validation_report_format", "json"),
    ],
)
def test_cmd_run_all_passes_runtime_options(
    monkeypatch, parser, option_args, keyword, expected
):
    calls = _capture_run_all_stacks_call(monkeypatch)

    args = parser.parse_args(["run-all", "plan", *option_args])

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 0
    assert calls["run"][2][keyword] == expected


def test_cmd_run_all_passes_tags(monkeypatch, parser):
    calls = _capture_run_all_stacks_call(monkeypatch)

    args = parser.parse_args(["run-all", "plan", "--tag", "prod", "--tag", "web"])

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 0
    assert calls["run"][2]["tags"] == ["prod", "web"]


def test_cmd_terragrunt_action_plan_destroy(monkeypatch, parser):
    calls = _capture_run_stack_action_call(monkeypatch)

    args = parser.parse_args(["plan", "stack.yaml", "--destroy"])

    exit_code = cli_main._cmd_terragrunt_action(args, "plan")

    assert exit_code == 0
    assert calls["run"][0] == "plan"
    assert calls["run"][2]["destroy"] is True


def test_cmd_operation_run_passes_runtime_flags(monkeypatch, parser, capsys):
    calls = _capture_run_stack_operation_call(monkeypatch)
    args = parser.parse_args(
        [
            "operation",
            "run",
            "deploy_app",
            "stack.yaml",
            "--no-cas",
            "--force-rerun",
        ]
    )

    exit_code = cli_main._cmd_operation_run(args)

    assert exit_code == 0
    stack_file, operation_name, kwargs = calls["run"]
    assert stack_file == Path("stack.yaml")
    assert operation_name == "deploy_app"
    assert kwargs["no_cas"] is True
    assert kwargs["force_rerun"] is True
    assert json.loads(capsys.readouterr().out) == {
        "exit_code": 0,
        "operation": "deploy_app",
    }


def test_operation_force_rerun_reads_environment(monkeypatch):
    monkeypatch.setenv("STACKSMITH_FORCE_RERUN", "1")

    args = stacksmith.cli.main._build_parser().parse_args(
        ["operation", "run", "deploy_app", "stack.yaml"]
    )

    assert args.force_rerun is True


@pytest.mark.parametrize(
    ("option_args", "keyword", "expected"),
    [
        (
            ["--tag", "prod", "--tag-expr", "contains(tags, 'prod')"],
            "tag_expr",
            "contains(tags, 'prod')",
        ),
        (["--save-plan-json", "plan.json"], "save_plan_json", Path("plan.json")),
        (["--fail-on-changes"], "fail_on_changes", True),
        (["--validation-report-format", "json"], "validation_report_format", "json"),
    ],
)
def test_cmd_terragrunt_action_passes_runtime_options(
    monkeypatch, parser, option_args, keyword, expected
):
    calls = _capture_run_stack_action_call(monkeypatch)

    args = parser.parse_args(["plan", "stack.yaml", *option_args])

    exit_code = cli_main._cmd_terragrunt_action(args, "plan")

    assert exit_code == 0
    assert calls["run"][2][keyword] == expected
    if "--tag" in option_args:
        assert calls["run"][2]["tags"] == ["prod"]


def test_cmd_validate_accepts_multiple_run_files(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def _fake_validate_stack(stack_file, **kwargs):
        calls["run"] = (stack_file, kwargs)
        return {"exit_code": 0}

    monkeypatch.delenv("STACKSMITH_STACK", raising=False)
    monkeypatch.setattr(cli_main, "validate_stack", _fake_validate_stack)
    monkeypatch.setattr(
        cli_main,
        "load_runfiles",
        lambda paths: RunFile(
            merge_mode="deep",
            stacks=[{"source": "local", "data": {"path": "./stack.yaml"}}],
        ),
    )

    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(
        [
            "validate",
            "--runfile",
            str(tmp_path / "base.yaml"),
            "--runfile",
            str(tmp_path / "override.yaml"),
        ]
    )

    exit_code = cli_main._cmd_validate(args)

    assert exit_code == 0
    assert render_file_reference(calls["run"][0]) == "./stack.yaml"
    assert calls["run"][1]["merge_mode"] == "deep"


def test_cmd_validate_passes_validation_report_format(monkeypatch, parser):
    calls: dict[str, object] = {}

    def _fake_validate_stack(stack_file, **kwargs):
        calls["run"] = (stack_file, kwargs)
        return {"exit_code": 0}

    monkeypatch.setattr(cli_main, "validate_stack", _fake_validate_stack)

    args = parser.parse_args(
        ["validate", "stack.yaml", "--validation-report-format", "json"]
    )

    exit_code = cli_main._cmd_validate(args)

    assert exit_code == 0
    assert calls["run"][0] == Path("stack.yaml")
    assert calls["run"][1]["validation_report_format"] == "json"


def test_cmd_validate_prepends_runfile_layers(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def _fake_validate_stack(stack_file, **kwargs):
        calls["run"] = (stack_file, kwargs)
        return {"exit_code": 0}

    monkeypatch.setattr(cli_main, "validate_stack", _fake_validate_stack)
    monkeypatch.setattr(
        cli_main,
        "load_runfiles",
        lambda paths: RunFile(
            merge_mode="override",
            stacks=[{"source": "local", "data": {"path": "./base-stack.yaml"}}],
            configs=[{"source": "local", "data": {"path": "./base-config.yaml"}}],
            vars=[
                {"source": "local", "data": {"path": "./base-vars.yaml"}},
                {"source": "inline", "data": {"replicas": 2}},
            ],
        ),
    )

    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(
        [
            "validate",
            "--runfile",
            str(tmp_path / "stacksmith.yaml"),
            "--stack",
            "./override-stack.yaml",
            "--config",
            "./override-config.yaml",
            "--vars",
            "./override-vars.yaml",
            "--var",
            "feature=true",
        ]
    )

    exit_code = cli_main._cmd_validate(args)

    assert exit_code == 0
    stack_refs = [
        render_file_reference(ref) if hasattr(ref, "source") else str(ref)
        for ref in calls["run"][0]
    ]
    assert stack_refs[:2] == ["./base-stack.yaml", "./override-stack.yaml"]

    config_refs = [
        render_file_reference(ref) if hasattr(ref, "source") else str(ref)
        for ref in calls["run"][1]["config"]
    ]
    assert config_refs == ["./base-config.yaml", "./override-config.yaml"]
    assert calls["run"][1]["merge_mode"] == "override"
    assert calls["run"][1]["vars_file"] == []
    rendered_layers = []
    for kind, value in calls["run"][1]["input_layers"]:
        if kind == "vars" and getattr(value, "source", None) != "inline":
            rendered_layers.append((kind, render_file_reference(value)))
        elif kind == "vars":
            rendered_layers.append((kind, {"source": value.source, "data": value.data}))
        else:
            rendered_layers.append((kind, value))
    assert rendered_layers == [
        ("vars", "./base-vars.yaml"),
        ("vars", {"source": "inline", "data": {"replicas": 2}}),
        ("vars", "./override-vars.yaml"),
        ("var", "feature=true"),
    ]


def test_cmd_terragrunt_action_uses_runner(monkeypatch):
    calls = _capture_run_stack_action_call(monkeypatch)

    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(["plan", "stack.yaml", "--destroy"])

    exit_code = cli_main._cmd_terragrunt_action(args, "plan")

    assert exit_code == 0
    assert calls["run"][0] == "plan"
    assert calls["run"][1] == Path("stack.yaml")
    assert calls["run"][2]["destroy"] is True


def test_cmd_run_all_passes_explicit_stacks_from_run_file(monkeypatch, tmp_path):
    calls = _capture_run_all_stacks_call(monkeypatch)
    monkeypatch.setattr(
        cli_main,
        "load_runfiles",
        lambda paths: RunFile(
            stacks=[
                {"source": "local", "data": {"path": "./network/stack.yaml"}},
                {"source": "local", "data": {"path": "./app/stack.yaml"}},
            ]
        ),
    )

    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(
        ["run-all", "plan", "--runfile", str(tmp_path / "stacksmith.yaml")]
    )

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 0
    assert [
        render_file_reference(ref) if hasattr(ref, "source") else str(ref)
        for ref in calls["run"][2]["stacks"]
    ] == ["./network/stack.yaml", "./app/stack.yaml"]


def test_cli_merge_mode_overrides_runfile(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def _fake_validate_stack(stack_file, **kwargs):
        calls["run"] = (stack_file, kwargs)
        return {"exit_code": 0}

    monkeypatch.setattr(cli_main, "validate_stack", _fake_validate_stack)
    monkeypatch.setattr(
        cli_main,
        "load_runfiles",
        lambda paths: RunFile(
            merge_mode="deep",
            merge_rules=[{"select": "address == '/items'", "mode": "override"}],
            stacks=[{"source": "local", "data": {"path": "./stack.yaml"}}],
        ),
    )

    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(
        [
            "validate",
            "--runfile",
            str(tmp_path / "stacksmith.yaml"),
            "--merge-mode",
            "override",
        ]
    )

    exit_code = cli_main._cmd_validate(args)

    assert exit_code == 0
    assert calls["run"][1]["merge_mode"] == "override"


def test_runfile_merge_rules_are_forwarded_as_policy(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def _fake_validate_stack(stack_file, **kwargs):
        calls["run"] = (stack_file, kwargs)
        return {"exit_code": 0}

    monkeypatch.setattr(cli_main, "validate_stack", _fake_validate_stack)
    monkeypatch.setattr(
        cli_main,
        "load_runfiles",
        lambda paths: RunFile(
            merge_mode="deep",
            merge_rules=[{"select": "address == '/items'", "mode": "override"}],
            stacks=[{"source": "local", "data": {"path": "./stack.yaml"}}],
        ),
    )

    args = stacksmith.cli.main._build_parser().parse_args(
        ["validate", "--runfile", str(tmp_path / "stacksmith.yaml")]
    )

    assert cli_main._cmd_validate(args) == 0
    policy = calls["run"][1]["merge_mode"]
    assert isinstance(policy, MergePolicy)
    assert policy.default == "deep"
    assert policy.rules[0].select == "address == '/items'"


def test_cmd_run_all_rejects_tag_expr_for_init(parser):
    args = parser.parse_args(
        ["run-all", "init", "--tag", "prod", "--tag-expr", "contains(tags, 'prod')"]
    )

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 1


def test_validation_report_format_rejects_csv(parser):
    with pytest.raises(SystemExit):
        parser.parse_args(["run-all", "plan", "--validation-report-format", "csv"])


def test_cmd_run_all_uses_ordered_runner(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def _fake_run_all_stacks(action, root, **kwargs):
        calls["run"] = (action, root, kwargs)
        return 17

    monkeypatch.setattr(cli_main, "run_all_stacks", _fake_run_all_stacks)

    args = SimpleNamespace(
        root=tmp_path,
        config=[str(tmp_path / "base.yaml"), str(tmp_path / "override.yaml")],
        vars_file=[str(tmp_path / "base-values.yaml"), str(tmp_path / "values.yaml")],
        vars=["bucket_name=my-bucket"],
        input_layers=[("var", "bucket_name=my-bucket")],
        build_dir=tmp_path / ".stacksmith",
        clean=True,
        action="plan",
        auto_approve=False,
        destroy=False,
        include_tag=None,
        exclude_tag=None,
        no_cache=False,
        use_local_modules=False,
        tag=None,
        tag_expr=None,
        save_plan_json=None,
        strict_validation_warnings=False,
        runfile=None,
        stack=None,
    )

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 17
    assert calls["run"][0] == "plan"
    assert calls["run"][1] == tmp_path
    assert calls["run"][2]["config"] == [
        str(tmp_path / "base.yaml"),
        str(tmp_path / "override.yaml"),
    ]
    assert calls["run"][2]["vars_file"] == [
        str(tmp_path / "base-values.yaml"),
        str(tmp_path / "values.yaml"),
    ]
    assert calls["run"][2]["input_layers"] == [("var", "bucket_name=my-bucket")]
    assert calls["run"][2]["build_dir"] == tmp_path / ".stacksmith"
    assert calls["run"][2]["clean"] is True


def test_cmd_run_all_preserves_interleaved_input_layers(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    def _fake_run_all_stacks(action, root, **kwargs):
        calls["run"] = (action, root, kwargs)
        return 0

    monkeypatch.setattr(cli_main, "run_all_stacks", _fake_run_all_stacks)

    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(
        [
            "run-all",
            "plan",
            "--vars",
            str(tmp_path / "base-values.yaml"),
            "--var",
            'beep={"nested": {"middle": true}}',
            "--vars",
            str(tmp_path / "values.yaml"),
        ]
    )

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 0
    assert calls["run"][2]["vars_file"] == []
    assert calls["run"][2]["input_layers"] == [
        ("vars", str(tmp_path / "base-values.yaml")),
        ("var", 'beep={"nested": {"middle": true}}'),
        ("vars", str(tmp_path / "values.yaml")),
    ]


def test_run_all_supports_common_args(monkeypatch, tmp_path, parser):
    monkeypatch.chdir(tmp_path)
    args = parser.parse_args(
        [
            "run-all",
            "plan",
            "--vars",
            str(tmp_path / "base.yaml"),
            "--vars",
            str(tmp_path / "values.yaml"),
            "--var",
            "bucket_name=my-bucket",
            "--config",
            "/tmp/config.yaml",
        ]
    )

    assert args.vars_file == [
        str(tmp_path / "base.yaml"),
        str(tmp_path / "values.yaml"),
    ]
    assert args.vars == ["bucket_name=my-bucket"]
    assert args.input_layers == [
        ("vars", str(tmp_path / "base.yaml")),
        ("vars", str(tmp_path / "values.yaml")),
        ("var", "bucket_name=my-bucket"),
    ]
    assert args.config == ["/tmp/config.yaml"]


def test_runtime_commands_support_no_cas_flag(parser):
    args = parser.parse_args(["plan", "stack.yaml", "--no-cas"])

    assert args.no_cas is True


def test_validate_stack_is_reusable_without_namespace(monkeypatch, tmp_path):
    calls: dict[str, object] = {}
    stack_path = tmp_path / "stack.yaml"

    def _fake_resolve_config_paths(config_args, cache_dir=None):
        calls["config_paths"] = (config_args, cache_dir)
        return [tmp_path / "stacksmith-config.yaml"]

    def _fake_load_config(config_paths, **kwargs):
        calls["load_config"] = config_paths
        return SimpleNamespace(
            remote_auth=None,
            var_validations={},
            source_path=tmp_path / "stacksmith-config.yaml",
        )

    def _fake_load_stack(stack_file, **kwargs):
        calls["stack_file"] = stack_file
        return SimpleNamespace(source_path=stack_path, name="example", tags=set())

    def _fake_resolve_inputs(*args, **kwargs):
        calls["resolve_inputs"] = kwargs
        return {}

    monkeypatch.setattr(api, "_resolve_config_paths", _fake_resolve_config_paths)
    monkeypatch.setattr(api, "load_config", _fake_load_config)
    monkeypatch.setattr(api, "load_stack", _fake_load_stack)
    monkeypatch.setattr(api, "load_stack_metadata", _fake_load_stack)
    monkeypatch.setattr(
        api, "_resolve_stack_paths", lambda path, cache_dir=None: [path]
    )
    monkeypatch.setattr(api, "resolve_inputs", _fake_resolve_inputs)

    report = api.validate_stack(
        stack_path,
        config=[str(tmp_path / "base.yaml")],
        vars_file=[str(tmp_path / "base-values.yaml"), str(tmp_path / "values.yaml")],
        input_layers=[("var", "bucket_name=my-bucket")],
        build_dir=tmp_path / ".stacksmith",
    )

    assert report["exit_code"] == 0
    assert calls["load_config"] == [tmp_path / "stacksmith-config.yaml"]
    assert calls["stack_file"] == stack_path
    assert calls["resolve_inputs"] == {
        "vars_file": [
            str(tmp_path / "base-values.yaml"),
            str(tmp_path / "values.yaml"),
        ],
        "input_layers": [("var", "bucket_name=my-bucket")],
        "config_validations": None,
        "config_validation_base_path": tmp_path,
        "cache_dir": tmp_path / ".stacksmith" / ".cache",
        "auth_config": None,
        "merge_mode": "deep",
        "context": {"stack": {"name": "example", "tags": []}},
    }


def test_package_exports_reusable_cli_functions():
    import stacksmith

    assert stacksmith.validate_stack is api.validate_stack
    assert stacksmith.generate_stack is api.generate_stack
    assert stacksmith.run_stack_action is api.run_stack_action
    assert stacksmith.run_all_stacks is api.run_all_stacks


def test_cli_uses_api_functions():
    assert cli_main.validate_stack is api.validate_stack
    assert cli_main.generate_stack is api.generate_stack
    assert cli_main.run_stack_action is api.run_stack_action
    assert cli_main.run_all_stacks is api.run_all_stacks


@pytest.mark.parametrize(
    ("env_value", "argv", "expected"),
    [
        (None, ["generate", "stack.yaml"], False),
        ("1", ["generate", "stack.yaml"], True),
        ("1", ["generate", "stack.yaml", "--no-local-modules"], False),
        (None, ["generate", "stack.yaml", "--use-local-modules"], True),
    ],
)
def test_use_local_modules_flag_resolution(
    monkeypatch,
    env_value,
    argv,
    expected,
):
    if env_value is None:
        monkeypatch.delenv("STACKSMITH_ONLY_USE_LOCAL_MODULES", raising=False)
    else:
        monkeypatch.setenv("STACKSMITH_ONLY_USE_LOCAL_MODULES", env_value)

    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(argv)
    assert args.use_local_modules is expected


def test_cmd_generate_passes_use_local_modules(monkeypatch, parser):
    calls: dict[str, object] = {}

    def _fake_generate_stack(stack_file, **kwargs):
        calls["gen"] = kwargs
        return 0

    monkeypatch.setattr(cli_main, "generate_stack", _fake_generate_stack)
    monkeypatch.delenv("STACKSMITH_ONLY_USE_LOCAL_MODULES", raising=False)

    args = parser.parse_args(["generate", "stack.yaml", "--use-local-modules"])
    cli_main._cmd_generate(args)

    assert calls["gen"]["use_local_modules"] is True


def test_cmd_run_all_passes_use_local_modules(monkeypatch):
    calls = _capture_run_all_stacks_call(monkeypatch)
    monkeypatch.setenv("STACKSMITH_ONLY_USE_LOCAL_MODULES", "1")

    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(["run-all", "plan"])
    cli_main._cmd_run_all(args)

    assert calls["run"][2]["use_local_modules"] is True


def test_cmd_diagnose_uses_api(monkeypatch):
    calls: dict[str, object] = {}

    def _fake_inspect_cache_diagnostics(stack_file, **kwargs):
        calls["diag"] = (stack_file, kwargs)
        return _diagnostics_payload()

    monkeypatch.setattr(
        cli_main, "inspect_cache_diagnostics", _fake_inspect_cache_diagnostics
    )

    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(["info", "diagnose", "stack.yaml"])
    exit_code = cli_main._cmd_diagnose(args)

    assert exit_code == 0
    assert calls["diag"][0] == Path("stack.yaml")
    assert calls["diag"][1]["config"] is None
    assert calls["diag"][1]["build_dir"] is None
    assert calls["diag"][1]["no_cache"] is False


def test_cmd_diagnose_json_emits_stdout(monkeypatch, parser, capsys):
    monkeypatch.setattr(
        cli_main,
        "inspect_cache_diagnostics",
        lambda *args, **kwargs: _diagnostics_payload(),
    )
    args = parser.parse_args(["info", "diagnose", "stack.yaml", "--format", "json"])

    exit_code = cli_main._cmd_diagnose(args)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["stack_file"] == "stack.yaml"
    assert captured.err == ""


def test_cmd_diagnose_table_emits_stderr(monkeypatch, parser, capsys):
    monkeypatch.setattr(
        cli_main,
        "inspect_cache_diagnostics",
        lambda *args, **kwargs: _diagnostics_payload(
            remote_cache_entries=[{"name": "foo", "type": "file"}]
        ),
    )
    args = parser.parse_args(["info", "diagnose", "stack.yaml", "--format", "table"])

    exit_code = cli_main._cmd_diagnose(args)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert "Stacksmith Diagnostics" in captured.err


def test_cmd_info_environments_emits_json(monkeypatch, parser, capsys):
    monkeypatch.setattr(
        cli_main,
        "inspect_environments",
        lambda **kwargs: {
            "gitops_root": "examples/gitops-repo",
            "discovery_mode": "env-files",
            "common_runfile": "examples/gitops-repo/common/stacksmith.yaml",
            "all_environments": ["dev", "prod"],
            "selected_environments": ["dev"],
            "changed_paths": ["examples/gitops-repo/environments/dev.yaml"],
            "matrix": [
                {
                    "environment": "dev",
                    "runfile": "examples/gitops-repo/common/stacksmith.yaml",
                    "environment_runfile": "examples/gitops-repo/environments/dev.yaml",
                }
            ],
        },
    )
    args = parser.parse_args(["info", "environments", "--format", "json"])

    exit_code = cli_main._cmd_info_environments(args)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["selected_environments"] == ["dev"]


def test_cmd_ci_validate_uses_api(monkeypatch, parser, capsys):
    calls: dict[str, object] = {}

    def _fake_validate_ci_inputs(**kwargs):
        calls["validate"] = kwargs
        return {
            "command": "ci validate",
            "status": "pass",
            "exit_code": 0,
            "summary": {"pass": 1, "fail": 0, "total": 1},
            "results": [
                {
                    "name": "discovery",
                    "status": "pass",
                    "message": "ok",
                    "detail": None,
                }
            ],
        }

    monkeypatch.setattr(cli_main, "validate_ci_inputs", _fake_validate_ci_inputs)
    args = parser.parse_args(
        [
            "ci",
            "validate",
            "--workflow-runfile",
            "common/stacksmith.yaml",
            "--workflow-env-file",
            "/dev/null",
            "--workflow-validation-report-format",
            "json",
        ]
    )

    exit_code = cli_main._cmd_ci_validate(args)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["status"] == "pass"
    assert calls["validate"]["runfile"] == "common/stacksmith.yaml"
    assert calls["validate"]["validation_report_format"] == "json"


def test_cmd_ci_validate_table_emits_stderr(monkeypatch, parser, capsys):
    monkeypatch.setattr(
        cli_main,
        "validate_ci_inputs",
        lambda **kwargs: {
            "command": "ci validate",
            "status": "pass",
            "exit_code": 0,
            "summary": {"pass": 1, "fail": 0, "total": 1},
            "results": [
                {
                    "name": "discovery",
                    "status": "pass",
                    "message": "ok",
                    "detail": {"mode": "env-files"},
                }
            ],
        },
    )
    args = parser.parse_args(["ci", "validate", "--format", "table"])

    exit_code = cli_main._cmd_ci_validate(args)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert "CI Validation" in captured.err


def test_ci_prepare_has_manifest_inputs(parser):
    args = parser.parse_args(
        [
            "ci",
            "prepare",
            "--command",
            "plan",
            "--config-ref",
            "platform/stacksmith-config.yaml",
            "--default-branch",
            "main",
        ]
    )

    assert args.ci_command == "prepare"
    assert args.config_ref == "platform/stacksmith-config.yaml"


def test_ci_prepare_from_env_has_adapter_inputs(parser):
    args = parser.parse_args(
        [
            "ci",
            "prepare-from-env",
            "--provider",
            "github-actions",
            "--manifest-file",
            "manifest.json",
            "--github-output",
            "github-output.txt",
        ]
    )

    assert args.ci_command == "prepare-from-env"
    assert args.provider == "github-actions"
    assert args.manifest_file == Path("manifest.json")
    assert args.github_output == Path("github-output.txt")


def test_ci_execute_from_env_has_adapter_inputs(parser):
    args = parser.parse_args(
        [
            "ci",
            "execute-from-env",
            "--provider",
            "jenkins",
            "--manifest-file",
            "manifest.json",
            "--environment",
            "dev",
            "--validation-report-output",
            "report.json",
        ]
    )

    assert args.ci_command == "execute-from-env"
    assert args.provider == "jenkins"
    assert args.manifest_file == Path("manifest.json")
    assert args.environment == "dev"
    assert args.validation_report_output == Path("report.json")


def test_cmd_ci_prepare_emits_manifest(monkeypatch, parser, capsys):
    from stacksmith.gitops.contracts import CiExecutionManifest, CiExecutionRow

    monkeypatch.setattr(
        cli_main,
        "prepare_ci_execution",
        lambda **kwargs: CiExecutionManifest(
            command="plan",
            config_ref="platform/stacksmith-config.yaml",
            matrix=[
                CiExecutionRow(
                    environment="dev",
                    runfile="common/stacksmith.yaml",
                    environment_runfile="environments/dev.yaml",
                )
            ],
        ),
    )
    args = parser.parse_args(
        [
            "ci",
            "prepare",
            "--command",
            "plan",
            "--config-ref",
            "platform/stacksmith-config.yaml",
        ]
    )

    exit_code = cli_main._cmd_ci_prepare(args)

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["matrix"][0]["environment"] == "dev"


def test_cmd_ci_prepare_from_env_emits_manifest(monkeypatch, parser, capsys):
    from stacksmith.gitops.contracts import CiExecutionManifest, CiExecutionRow

    monkeypatch.setattr(
        cli_main,
        "prepare_ci_execution",
        lambda **kwargs: CiExecutionManifest(
            command="plan",
            config_ref="platform/stacksmith-config.yaml",
            matrix=[
                CiExecutionRow(
                    environment="dev",
                    runfile="common/stacksmith.yaml",
                    environment_runfile="environments/dev.yaml",
                )
            ],
        ),
    )
    monkeypatch.setenv("INPUT_COMMAND", "plan")
    monkeypatch.setenv("INPUT_CONFIG_REF", "platform/stacksmith-config.yaml")
    args = parser.parse_args(["ci", "prepare-from-env", "--provider", "jenkins"])

    exit_code = cli_main._cmd_ci_prepare_from_env(args)

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["matrix"][0]["environment"] == "dev"


def test_cmd_ci_prepare_from_env_writes_github_outputs(monkeypatch, parser, tmp_path):
    from stacksmith.gitops.contracts import CiExecutionManifest, CiExecutionRow

    monkeypatch.setattr(
        cli_main,
        "prepare_ci_execution",
        lambda **kwargs: CiExecutionManifest(
            command="plan",
            config_ref="platform/stacksmith-config.yaml",
            matrix=[
                CiExecutionRow(
                    environment="dev",
                    runfile="common/stacksmith.yaml",
                    environment_runfile="environments/dev.yaml",
                )
            ],
        ),
    )
    monkeypatch.setenv("INPUT_COMMAND", "plan")
    monkeypatch.setenv("INPUT_CONFIG_REF", "platform/stacksmith-config.yaml")
    github_output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    args = parser.parse_args(["ci", "prepare-from-env", "--provider", "github-actions"])

    exit_code = cli_main._cmd_ci_prepare_from_env(args)

    assert exit_code == 0
    output_lines = github_output.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("manifest=") for line in output_lines)
    assert any(line.startswith("matrix=") for line in output_lines)
    assert "count=1" in output_lines


def test_cmd_ci_execute_reuses_plan_handler(monkeypatch, parser, tmp_path: Path):
    from stacksmith.gitops.contracts import CiExecutionManifest, CiExecutionRow

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        CiExecutionManifest(
            command="plan",
            config_ref="platform/stacksmith-config.yaml",
            matrix=[
                CiExecutionRow(environment="dev", runfile="common/stacksmith.yaml")
            ],
            no_cas=True,
            fail_on_changes=True,
        ).model_dump_json(),
        encoding="utf-8",
    )
    calls: dict[str, object] = {}

    def _fake_plan_handler(args, command):
        calls["command"] = command
        calls["args"] = args
        return 0

    monkeypatch.setattr(cli_main, "_cmd_terragrunt_action", _fake_plan_handler)
    args = parser.parse_args(
        ["ci", "execute", "--manifest", str(manifest_path), "--environment", "dev"]
    )

    assert cli_main._cmd_ci_execute(args) == 0
    assert calls["command"] == "plan"
    assert calls["args"].config == ["platform/stacksmith-config.yaml"]
    assert calls["args"].no_cas is True
    assert calls["args"].fail_on_changes is True


def test_cmd_ci_execute_from_env_uses_manifest_env(monkeypatch, parser, tmp_path: Path):
    from stacksmith.gitops.contracts import CiExecutionManifest, CiExecutionRow

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        CiExecutionManifest(
            command="plan",
            config_ref="platform/stacksmith-config.yaml",
            matrix=[
                CiExecutionRow(environment="dev", runfile="common/stacksmith.yaml")
            ],
            validation_report_format="json",
        ).model_dump_json(),
        encoding="utf-8",
    )
    calls: dict[str, object] = {}

    def _fake_run_ci_execute(manifest, environment, validation_report_output):
        calls["manifest"] = manifest
        calls["environment"] = environment
        calls["validation_report_output"] = validation_report_output
        return 0

    monkeypatch.setattr(cli_main, "_run_ci_execute", _fake_run_ci_execute)
    monkeypatch.setenv("CI_MANIFEST_FILE", str(manifest_path))
    monkeypatch.setenv("ENVIRONMENT", "dev")
    args = parser.parse_args(
        [
            "ci",
            "execute-from-env",
            "--provider",
            "jenkins",
        ]
    )

    exit_code = cli_main._cmd_ci_execute_from_env(args)

    assert exit_code == 0
    assert calls["environment"] == "dev"
    assert calls["validation_report_output"] == Path(
        ".stacksmith-ci/dev/validation-report.json"
    )


def test_cmd_inspect_json_emits_stdout(monkeypatch, parser, capsys):
    result = ComponentTypeInfo(
        component_type="aws_s3_bucket",
        display_name="AWS S3 bucket",
        module_source="https://github.com/org/s3.git",
        module_version="1.0.0",
        auto_inject=False,
        inputs=[InputInfo(name="bucket_name", module_variable="bucket_name")],
    )
    monkeypatch.setattr(cli_main, "inspect_modules", lambda **kwargs: ([result], []))

    args = parser.parse_args(["info", "inspect", "--format", "json"])
    exit_code = cli_main._cmd_inspect(args)

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert exit_code == 0
    assert "aws_s3_bucket" in parsed
    assert captured.err == ""


def test_cmd_inspect_table_emits_stderr(monkeypatch, parser, capsys):
    result = ComponentTypeInfo(
        component_type="aws_s3_bucket",
        display_name="AWS S3 bucket",
        module_source="https://github.com/org/s3.git",
        module_version="1.0.0",
        auto_inject=False,
        inputs=[InputInfo(name="bucket_name", module_variable="bucket_name")],
    )
    monkeypatch.setattr(cli_main, "inspect_modules", lambda **kwargs: ([result], []))

    args = parser.parse_args(["info", "inspect", "--format", "table"])
    exit_code = cli_main._cmd_inspect(args)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert "AWS S3 bucket" in captured.err
