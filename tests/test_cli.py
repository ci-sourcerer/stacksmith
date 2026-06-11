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
from stacksmith.models import RunFile, render_file_reference


@pytest.fixture
def parser():
    return stacksmith.cli.main._build_parser()


def _capture_run_all_stacks_call(
    monkeypatch: pytest.MonkeyPatch,
    *,
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
    *,
    return_code: int = 0,
) -> dict[str, object]:
    calls: dict[str, object] = {}

    def _fake_run_stack_action(action, stack_file, **kwargs):
        calls["run"] = (action, stack_file, kwargs)
        return return_code

    monkeypatch.setattr(cli_main, "run_stack_action", _fake_run_stack_action)
    return calls


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
        ["validate", "stack.yaml", "--validation-report-format", "csv"]
    )

    assert args.validation_report_format == "csv"


def test_plan_subcommand_supports_validation_report_format(parser):
    args = parser.parse_args(
        ["plan", "stack.yaml", "--validation-report-format", "csv"]
    )

    assert args.validation_report_format == "csv"


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
    args = parser.parse_args(["run-all", "plan", "--validation-report-format", "csv"])

    assert args.validation_report_format == "csv"


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


def test_cmd_run_all_passes_tag_expr(monkeypatch, parser):
    calls = _capture_run_all_stacks_call(monkeypatch)

    args = parser.parse_args(
        ["run-all", "plan", "--tag-expr", "contains(tags, 'prod')"]
    )

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 0
    assert calls["run"][2]["tag_expr"] == "contains(tags, 'prod')"


def test_cmd_run_all_passes_save_plan_json(monkeypatch, tmp_path, parser):
    calls = _capture_run_all_stacks_call(monkeypatch)

    args = parser.parse_args(
        ["run-all", "plan", "--save-plan-json", str(tmp_path / "plans")]
    )

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 0
    assert calls["run"][2]["save_plan_json"] == tmp_path / "plans"


def test_cmd_run_all_passes_fail_on_changes(monkeypatch, parser):
    calls = _capture_run_all_stacks_call(monkeypatch)

    args = parser.parse_args(["run-all", "plan", "--fail-on-changes"])

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 0
    assert calls["run"][2]["fail_on_changes"] is True


def test_cmd_run_all_passes_validation_report_format(monkeypatch, parser):
    calls = _capture_run_all_stacks_call(monkeypatch)

    args = parser.parse_args(["run-all", "plan", "--validation-report-format", "csv"])

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 0
    assert calls["run"][2]["validation_report_format"] == "csv"


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


def test_cmd_terragrunt_action_passes_tag_expr(monkeypatch, parser):
    calls = _capture_run_stack_action_call(monkeypatch)

    args = parser.parse_args(
        [
            "apply",
            "stack.yaml",
            "--tag",
            "prod",
            "--tag-expr",
            "contains(tags, 'prod')",
        ]
    )

    exit_code = cli_main._cmd_terragrunt_action(args, "apply")

    assert exit_code == 0
    assert calls["run"][2]["tags"] == ["prod"]
    assert calls["run"][2]["tag_expr"] == "contains(tags, 'prod')"


def test_cmd_terragrunt_action_passes_save_plan_json(monkeypatch, tmp_path, parser):
    calls = _capture_run_stack_action_call(monkeypatch)

    args = parser.parse_args(
        ["plan", "stack.yaml", "--save-plan-json", str(tmp_path / "plan.json")]
    )

    exit_code = cli_main._cmd_terragrunt_action(args, "plan")

    assert exit_code == 0
    assert calls["run"][2]["save_plan_json"] == tmp_path / "plan.json"


def test_cmd_terragrunt_action_passes_fail_on_changes(monkeypatch, parser):
    calls = _capture_run_stack_action_call(monkeypatch)

    args = parser.parse_args(["plan", "stack.yaml", "--fail-on-changes"])

    exit_code = cli_main._cmd_terragrunt_action(args, "plan")

    assert exit_code == 0
    assert calls["run"][2]["fail_on_changes"] is True


def test_cmd_terragrunt_action_passes_validation_report_format(monkeypatch, parser):
    calls = _capture_run_stack_action_call(monkeypatch)

    args = parser.parse_args(
        ["plan", "stack.yaml", "--validation-report-format", "csv"]
    )

    exit_code = cli_main._cmd_terragrunt_action(args, "plan")

    assert exit_code == 0
    assert calls["run"][2]["validation_report_format"] == "csv"


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
        ["validate", "stack.yaml", "--validation-report-format", "csv"]
    )

    exit_code = cli_main._cmd_validate(args)

    assert exit_code == 0
    assert calls["run"][0] == Path("stack.yaml")
    assert calls["run"][1]["validation_report_format"] == "csv"


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
            vars=[{"source": "local", "data": {"path": "./base-vars.yaml"}}],
            var={"replicas": 2},
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
        if kind == "vars" and hasattr(value, "source"):
            rendered_layers.append((kind, render_file_reference(value)))
        else:
            rendered_layers.append((kind, value))
    assert rendered_layers == [
        ("vars", "./base-vars.yaml"),
        ("var", "replicas=2"),
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


def test_cmd_run_all_rejects_tag_expr_for_init(parser):
    args = parser.parse_args(
        ["run-all", "init", "--tag", "prod", "--tag-expr", "contains(tags, 'prod')"]
    )

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 1


def test_cmd_run_all_rejects_validation_report_format_for_non_plan(parser):
    args = parser.parse_args(["run-all", "apply", "--validation-report-format", "csv"])

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 1


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
        return SimpleNamespace(source_path=stack_path)

    def _fake_resolve_inputs(*args, **kwargs):
        calls["resolve_inputs"] = kwargs
        return {}

    monkeypatch.setattr(api, "_resolve_config_paths", _fake_resolve_config_paths)
    monkeypatch.setattr(api, "load_config", _fake_load_config)
    monkeypatch.setattr(api, "load_stack", _fake_load_stack)
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

    def _fake_diagnose_cache(stack_file, **kwargs):
        calls["diag"] = (stack_file, kwargs)
        return 0

    monkeypatch.setattr(cli_main, "diagnose_cache", _fake_diagnose_cache)

    parser = stacksmith.cli.main._build_parser()
    args = parser.parse_args(["info", "diagnose", "stack.yaml"])
    exit_code = cli_main._cmd_diagnose(args)

    assert exit_code == 0
    assert calls["diag"][0] == Path("stack.yaml")
    assert calls["diag"][1]["config"] is None
    assert calls["diag"][1]["build_dir"] is None
    assert calls["diag"][1]["no_cache"] is False


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
