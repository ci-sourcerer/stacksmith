from pathlib import Path
from types import SimpleNamespace

from stacksmith import runner
from stacksmith.loader import load_config
from stacksmith.models import PlanValidation, ValidationSpec
from stacksmith.validation import PlanValidationOutcome


class FakeVersionResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _command_name(cmd: list[str]) -> str:
    return Path(cmd[0]).name if cmd else ""


def _matches_command(cmd: list[str], tool_name: str, *args: str) -> bool:
    return _command_name(cmd) == tool_name and cmd[1 : 1 + len(args)] == list(args)


def _supported_tool_version_result(cmd: list[str]) -> FakeVersionResult | None:
    if _matches_command(cmd, "terragrunt", "--version"):
        return FakeVersionResult(returncode=0, stdout="terragrunt version v1.5.0")
    if _matches_command(cmd, "tofu", "-version"):
        return FakeVersionResult(returncode=0, stdout="tofu v1.5.0")
    return None


def _stack_dirs(
    tmp_path: Path,
    names: tuple[str, ...] = ("vpc", "rds", "web"),
) -> dict[str, Path]:
    all_dirs = {
        "vpc": tmp_path / "networking" / "vpc",
        "rds": tmp_path / "data" / "rds",
        "web": tmp_path / "compute" / "web",
    }
    return {name: all_dirs[name] for name in names}


def _patch_run_terragrunt(monkeypatch, handler):
    def _fake_run_terragrunt(
        args: list[str],
        working_dir: Path,
        auto_approve: bool = False,
        config=None,
        stack_name: str | None = None,
        cache_dir=None,
        auth_config=None,
        save_plan_json=None,
        strict_validation_warnings: bool = False,
        fail_on_changes: bool = False,
        plan_validation_results=None,
        no_cas: bool = False,
    ) -> int:
        return handler(
            args=args,
            working_dir=working_dir,
            auto_approve=auto_approve,
            save_plan_json=save_plan_json,
            strict_validation_warnings=strict_validation_warnings,
            plan_validation_results=plan_validation_results,
            no_cas=no_cas,
        )

    monkeypatch.setattr(runner, "run_terragrunt", _fake_run_terragrunt)


def test_run_terragrunt_streaming_routes_stdout_to_stderr(monkeypatch, tmp_path):
    calls: dict[str, object] = {}

    class FakeResult:
        returncode = 0

    def _fake_subprocess_run(*args, **kwargs):
        calls["kwargs"] = kwargs
        return FakeResult()

    monkeypatch.setattr(runner, "subprocess", SimpleNamespace(run=_fake_subprocess_run))

    exit_code = runner._run_terragrunt_streaming(["terragrunt", "plan"], tmp_path)

    assert exit_code == 0
    assert calls["kwargs"]["stdout"] is runner.sys.stderr
    assert calls["kwargs"]["stderr"] is runner.sys.stderr


def test_run_terragrunt_all_ordered_dependency_first(monkeypatch, tmp_path):
    calls: list[tuple[list[str], Path, bool]] = []

    def _handler(**kwargs) -> int:
        calls.append((kwargs["args"], kwargs["working_dir"], kwargs["auto_approve"]))
        return 0

    _patch_run_terragrunt(monkeypatch, _handler)

    exit_code = runner.run_terragrunt_all_ordered("plan", _stack_dirs(tmp_path))

    assert exit_code == 0
    assert calls == [
        (["plan"], tmp_path / "networking" / "vpc", False),
        (["plan"], tmp_path / "data" / "rds", False),
        (["plan"], tmp_path / "compute" / "web", False),
    ]


def test_run_terragrunt_all_ordered_destroy_reverses_order(monkeypatch, tmp_path):
    calls: list[Path] = []

    def _handler(**kwargs) -> int:
        calls.append(kwargs["working_dir"])
        return 0

    _patch_run_terragrunt(monkeypatch, _handler)

    exit_code = runner.run_terragrunt_all_ordered("destroy", _stack_dirs(tmp_path))

    assert exit_code == 0
    assert calls == [
        tmp_path / "compute" / "web",
        tmp_path / "data" / "rds",
        tmp_path / "networking" / "vpc",
    ]


def test_run_terragrunt_all_ordered_stops_on_first_failure(monkeypatch, tmp_path):
    calls: list[Path] = []

    def _handler(**kwargs) -> int:
        calls.append(kwargs["working_dir"])
        if kwargs["working_dir"] == tmp_path / "data" / "rds":
            return 2
        return 0

    _patch_run_terragrunt(monkeypatch, _handler)

    exit_code = runner.run_terragrunt_all_ordered("init", _stack_dirs(tmp_path))

    assert exit_code == 2
    assert calls == [
        tmp_path / "networking" / "vpc",
        tmp_path / "data" / "rds",
    ]


def test_run_terragrunt_all_ordered_uses_stack_specific_args(monkeypatch, tmp_path):
    calls: list[tuple[list[str], Path]] = []

    def _handler(**kwargs) -> int:
        calls.append((kwargs["args"], kwargs["working_dir"]))
        return 0

    _patch_run_terragrunt(monkeypatch, _handler)

    stack_dirs = _stack_dirs(tmp_path)
    stack_args = {
        "vpc": ["plan", "-target", "module.main-vpc"],
        "web": ["plan", "-target", "module.web-server"],
    }

    exit_code = runner.run_terragrunt_all_ordered(
        "plan",
        stack_dirs,
        stack_args_by_name=stack_args,
    )

    assert exit_code == 0
    assert calls == [
        (["plan", "-target", "module.main-vpc"], tmp_path / "networking" / "vpc"),
        (["plan", "-target", "module.web-server"], tmp_path / "compute" / "web"),
    ]


def test_run_terragrunt_adds_no_cas_flag(monkeypatch, tmp_path):
    seen_commands: list[list[str]] = []

    def _fake_subprocess_run(cmd, **kwargs):
        if _matches_command(cmd, "terragrunt", "--version") or _matches_command(
            cmd, "tofu", "-version"
        ):
            return _supported_tool_version_result(cmd)

        seen_commands.append(cmd)
        return FakeVersionResult(returncode=0, stdout="terragrunt plan simulated")

    monkeypatch.setattr(runner, "subprocess", SimpleNamespace(run=_fake_subprocess_run))
    runner._TOOL_VERSION_CHECKED = False

    exit_code = runner.run_terragrunt(["plan"], tmp_path, no_cas=True)

    assert exit_code == 0
    assert seen_commands == [["terragrunt", "--no-cas", "plan"]]


def test_check_plan_validations_uses_config_base_path(tmp_path):
    validators_dir = tmp_path / "validators"
    validators_dir.mkdir()
    (validators_dir / "plan_rule.py").write_text(
        "def validate(value, **context):\n"
        "    return 'pass' if value['planned_values']['ok'] is True else 'fail'\n",
        encoding="utf-8",
    )
    config_file = tmp_path / "stacksmith-config.yaml"
    config_file.write_text(
        "backend:\n"
        "  type: s3\n"
        "  bucket: test-state-bucket\n"
        "  region: us-east-1\n"
        "tools:\n"
        "  tofu:\n"
        "    version: '1.8.0'\n"
        "  terragrunt:\n"
        "    version: '1.0.6'\n"
        "provider_mappings:\n"
        "  aws:\n"
        "    source:\n"
        "      source: registry\n"
        "      data:\n"
        "        address: hashicorp/aws\n"
        "        version: '~> 5.0'\n"
        "    instances:\n"
        "      default:\n"
        "        config:\n"
        "          data:\n"
        "            region: us-east-1\n"
        "module_mappings:\n"
        "  aws_s3_bucket:\n"
        "    source:\n"
        "      source: git\n"
        "      data:\n"
        "        repo: https://github.com/org/terraform-aws-s3.git\n"
        "        ref: '1.0.0'\n"
        "plan_validations:\n"
        "  no_bad_plan:\n"
        "    rule:\n"
        "      script:\n"
        "        source: local\n"
        "        data:\n"
        "          path: validators/plan_rule.py\n",
        encoding="utf-8",
    )
    config = load_config(config_file)

    results = runner.check_plan_validations(
        config,
        {"planned_values": {"ok": True}},
        stack_name="my-stack",
    )

    assert len(results) == 1
    assert results[0].name == "no_bad_plan"
    assert results[0].status == "pass"


def test_run_terragrunt_plan_invokes_plan_validation_path(monkeypatch, tmp_path):
    calls: dict[str, object] = {}
    report_results: list[runner.PlanValidationResult] = []

    def _fake_run_plan_validations(
        plan_cmd: list[str],
        args: list[str],
        working_dir: Path,
        config,
        stack_name: str,
        cache_dir=None,
        auth_config=None,
        save_plan_json=None,
        strict_validation_warnings: bool = False,
        fail_on_changes: bool = False,
        plan_validation_results=None,
    ) -> int:
        calls["plan_cmd"] = plan_cmd
        calls["args"] = args
        calls["working_dir"] = working_dir
        calls["stack_name"] = stack_name
        calls["save_plan_json"] = save_plan_json
        calls["strict_validation_warnings"] = strict_validation_warnings
        calls["plan_validation_results"] = plan_validation_results
        return 0

    monkeypatch.setattr(runner, "_run_plan_validations", _fake_run_plan_validations)
    monkeypatch.setattr(
        runner,
        "subprocess",
        SimpleNamespace(
            run=lambda cmd, **kwargs: _supported_tool_version_result(cmd)
            or FakeVersionResult(returncode=0, stdout="terragrunt plan simulated")
        ),
    )
    runner._TOOL_VERSION_CHECKED = False

    config = SimpleNamespace(
        plan_validations={"check": PlanValidation(rule=ValidationSpec(inline="'pass'"))}
    )
    exit_code = runner.run_terragrunt(
        ["plan"],
        tmp_path,
        config=config,
        stack_name="web",
        plan_validation_results=report_results,
    )

    assert exit_code == 0
    assert calls["plan_cmd"][1:] == ["plan"]
    assert Path(calls["plan_cmd"][0]).name == "terragrunt"
    assert calls["args"] == ["plan"]
    assert calls["working_dir"] == tmp_path
    assert calls["stack_name"] == "web"
    assert calls["save_plan_json"] is None
    assert calls["strict_validation_warnings"] is False
    assert calls["plan_validation_results"] is report_results


def test_run_terragrunt_plan_destroy_skips_plan_validations(monkeypatch, tmp_path):
    def _fake_run_plan_validations(*args, **kwargs):
        raise AssertionError("Plan validations should not run for destroy plans")

    monkeypatch.setattr(runner, "_run_plan_validations", _fake_run_plan_validations)

    class FakeResult:
        returncode = 0

    def _fake_subprocess_run(cmd, **kwargs):
        if _matches_command(cmd, "terragrunt", "--version") or _matches_command(
            cmd, "tofu", "-version"
        ):
            return _supported_tool_version_result(cmd)
        return FakeVersionResult(returncode=0, stdout="terragrunt plan simulated")

    monkeypatch.setattr(runner, "subprocess", SimpleNamespace(run=_fake_subprocess_run))
    runner._TOOL_VERSION_CHECKED = False

    config = SimpleNamespace(
        plan_validations={"check": PlanValidation(rule=ValidationSpec(inline="'pass'"))}
    )
    exit_code = runner.run_terragrunt(
        ["plan", "-destroy"],
        tmp_path,
        config=config,
        stack_name="web",
    )

    assert exit_code == 0


def test_run_terragrunt_strict_warning_mode_fails_on_warning(monkeypatch, tmp_path):
    class FakeResult:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_subprocess_run(cmd, **kwargs):
        if _matches_command(cmd, "terragrunt", "--version") or _matches_command(
            cmd, "tofu", "-version"
        ):
            return _supported_tool_version_result(cmd)
        if _matches_command(cmd, "terragrunt", "plan"):
            return FakeResult(returncode=0)
        if _matches_command(cmd, "terragrunt", "show", "-json"):
            return FakeResult(returncode=0, stdout='{"planned_values": {"ok": true}}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(runner, "subprocess", SimpleNamespace(run=_fake_subprocess_run))
    runner._TOOL_VERSION_CHECKED = False
    monkeypatch.setattr(
        runner,
        "check_plan_validations",
        lambda *args, **kwargs: [
            runner.PlanValidationResult(
                name="warn_rule",
                status=PlanValidationOutcome.WARN,
                message="warning from policy",
                stack_name="web",
            )
        ],
    )

    exit_code = runner.run_terragrunt(
        ["plan"],
        tmp_path,
        config=SimpleNamespace(
            plan_validations={
                "warn_rule": PlanValidation(rule=ValidationSpec(inline="'pass'"))
            }
        ),
        stack_name="web",
        strict_validation_warnings=True,
    )

    assert exit_code == 1


def test_run_terragrunt_delegates_plan_result_processing(monkeypatch, tmp_path):
    class FakeResult:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls: dict[str, object] = {}

    def _fake_subprocess_run(cmd, **kwargs):
        if _matches_command(cmd, "terragrunt", "--version") or _matches_command(
            cmd, "tofu", "-version"
        ):
            return _supported_tool_version_result(cmd)
        if _matches_command(cmd, "terragrunt", "plan"):
            return FakeResult(returncode=0)
        if _matches_command(cmd, "terragrunt", "show", "-json"):
            return FakeResult(returncode=0, stdout='{"planned_values": {"ok": true}}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(runner, "subprocess", SimpleNamespace(run=_fake_subprocess_run))
    runner._TOOL_VERSION_CHECKED = False
    monkeypatch.setattr(
        runner,
        "check_plan_validations",
        lambda *args, **kwargs: [
            runner.PlanValidationResult(
                name="warn_rule",
                status=PlanValidationOutcome.WARN,
                message="warning from policy",
                stack_name="web",
            )
        ],
    )

    def _fake_process_plan_validation_results(results, strict_validation_warnings):
        calls["results"] = results
        calls["strict_validation_warnings"] = strict_validation_warnings
        return 17

    monkeypatch.setattr(
        runner,
        "process_plan_validation_results",
        _fake_process_plan_validation_results,
    )

    exit_code = runner.run_terragrunt(
        ["plan"],
        tmp_path,
        config=SimpleNamespace(
            plan_validations={
                "warn_rule": PlanValidation(rule=ValidationSpec(inline="'pass'"))
            }
        ),
        stack_name="web",
        strict_validation_warnings=True,
    )

    assert exit_code == 17
    assert calls["strict_validation_warnings"] is True
    assert len(calls["results"]) == 1


def test_run_terragrunt_saves_plan_json(monkeypatch, tmp_path):
    class FakeResult:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    calls: list[list[str]] = []

    def _fake_subprocess_run(cmd, **kwargs):
        if _matches_command(cmd, "terragrunt", "--version") or _matches_command(
            cmd, "tofu", "-version"
        ):
            return _supported_tool_version_result(cmd)
        calls.append(cmd)
        if _matches_command(cmd, "terragrunt", "plan"):
            return FakeResult(returncode=0)
        if _matches_command(cmd, "terragrunt", "show", "-json"):
            return FakeResult(returncode=0, stdout='{"planned_values": {"ok": true}}')
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(runner, "subprocess", SimpleNamespace(run=_fake_subprocess_run))
    runner._TOOL_VERSION_CHECKED = False

    output_path = tmp_path / "saved-plan.json"
    exit_code = runner.run_terragrunt(
        ["plan"],
        tmp_path,
        config=None,
        stack_name="web",
        save_plan_json=output_path,
    )

    assert exit_code == 0
    assert output_path.exists()
    assert '"ok": true' in output_path.read_text(encoding="utf-8")


def test_run_terragrunt_fail_on_changes(monkeypatch, tmp_path):
    class FakeResult:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_subprocess_run(cmd, **kwargs):
        if _matches_command(cmd, "terragrunt", "--version") or _matches_command(
            cmd, "tofu", "-version"
        ):
            return _supported_tool_version_result(cmd)
        if _matches_command(cmd, "terragrunt", "plan"):
            return FakeResult(returncode=0)
        if _matches_command(cmd, "terragrunt", "show", "-json"):
            return FakeResult(
                returncode=0,
                stdout='{"resource_changes": [{"address": "aws_s3_bucket.example", "change": {"actions": ["create"]}}]}',
            )
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(runner, "subprocess", SimpleNamespace(run=_fake_subprocess_run))
    runner._TOOL_VERSION_CHECKED = False

    exit_code = runner.run_terragrunt(
        ["plan"],
        tmp_path,
        config=None,
        stack_name="web",
        fail_on_changes=True,
    )

    assert exit_code == 1


def test_run_terragrunt_fail_on_changes_no_change(monkeypatch, tmp_path):
    class FakeResult:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_subprocess_run(cmd, **kwargs):
        if _matches_command(cmd, "terragrunt", "--version") or _matches_command(
            cmd, "tofu", "-version"
        ):
            return _supported_tool_version_result(cmd)
        if _matches_command(cmd, "terragrunt", "plan"):
            return FakeResult(returncode=0)
        if _matches_command(cmd, "terragrunt", "show", "-json"):
            return FakeResult(
                returncode=0,
                stdout='{"resource_changes": [{"address": "aws_s3_bucket.example", "change": {"actions": ["no-op"]}}]}',
            )
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(runner, "subprocess", SimpleNamespace(run=_fake_subprocess_run))
    runner._TOOL_VERSION_CHECKED = False

    exit_code = runner.run_terragrunt(
        ["plan"],
        tmp_path,
        config=None,
        stack_name="web",
        fail_on_changes=True,
    )

    assert exit_code == 0


def test_run_terragrunt_all_ordered_saves_stack_specific_plan_json(
    monkeypatch,
    tmp_path,
):
    calls: list[Path | None] = []

    def _handler(**kwargs) -> int:
        calls.append(kwargs["save_plan_json"])
        return 0

    _patch_run_terragrunt(monkeypatch, _handler)

    stack_dirs = _stack_dirs(tmp_path, names=("vpc", "web"))
    output_dir = tmp_path / "plans"

    exit_code = runner.run_terragrunt_all_ordered(
        "plan",
        stack_dirs,
        save_plan_json=output_dir,
    )

    assert exit_code == 0
    assert calls == [output_dir / "vpc.json", output_dir / "web.json"]


def test_check_required_tool_versions_runs_both_tools(monkeypatch):
    calls: list[object] = []

    def _fake_resolve_toolchain(*args, **kwargs):
        calls.append((args, kwargs))
        return runner.ResolvedToolchain(tofu="/tmp/tofu", terragrunt="/tmp/terragrunt")

    monkeypatch.setattr(runner, "resolve_toolchain", _fake_resolve_toolchain)
    runner._TOOL_VERSION_CHECKED = False

    runner._check_required_tool_versions()

    assert len(calls) == 1
    assert calls[0][0] == (None, None, None)
    assert calls[0][1]["subprocess_module"] is runner.subprocess
    assert runner._RESOLVED_TOOLCHAIN.tofu == "/tmp/tofu"
    assert runner._RESOLVED_TOOLCHAIN.terragrunt == "/tmp/terragrunt"
    assert runner._TOOL_VERSION_CHECKED is True


def test_check_required_tool_versions_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("STACKSMITH_SKIP_TOOL_VERSION_CHECK", "1")
    calls: list[list[str]] = []

    def _fake_subprocess_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeVersionResult(returncode=0, stdout="ignored")

    monkeypatch.setattr(runner, "subprocess", SimpleNamespace(run=_fake_subprocess_run))
    runner._TOOL_VERSION_CHECKED = False

    runner._check_required_tool_versions()

    assert calls == []
