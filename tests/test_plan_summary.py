import json
from types import SimpleNamespace

import pytest

from stacksmith import runner
from stacksmith.plan_summary import (
    active_plan_summary,
    plan_summary_session,
    summarize_plan,
)


def _change(address="module.api.aws_instance.app", actions=None, **kwargs):
    return {
        "address": address,
        "type": "aws_instance",
        "mode": "managed",
        "change": {"actions": actions or ["create"]},
        **kwargs,
    }


def _plan(*resources, **kwargs):
    return {"format_version": "1.0", "resource_changes": list(resources), **kwargs}


@pytest.mark.parametrize(
    ("actions", "category"),
    [
        (["create"], "create"),
        (["update"], "update"),
        (["delete"], "destroy"),
        (["create", "delete"], "replace"),
        (["delete", "create"], "replace"),
        (["forget"], "forget"),
        (["future-action"], "unknown"),
    ],
)
def test_counts_resource_instances_once(actions, category):
    result = summarize_plan(_plan(_change(actions=actions)), ["api"])
    assert result["totals"][category] == 1
    assert result["totals"]["resources"] == 1
    assert result["resources"][0]["actions"] == actions
    assert result["complete"] is (category != "unknown")
    if category == "replace":
        assert result["totals"]["create"] == result["totals"]["destroy"] == 0


def test_nested_indexed_components_and_root_resources():
    result = summarize_plan(
        _plan(
            _change('module.api["blue"].module.child.aws_instance.app[0]'),
            _change("aws_instance.root"),
            _change("module.removed.aws_instance.old", ["delete"]),
            _change("module.idle.aws_instance.no_change", ["no-op"]),
        ),
        ["api", "idle"],
    )
    assert {item["address"]: item["component"] for item in result["resources"]} == {
        "aws_instance.root": None,
        'module.api["blue"].module.child.aws_instance.app[0]': "api",
        "module.removed.aws_instance.old": None,
    }
    assert [item["name"] for item in result["components"]] == ["api", "idle", None]
    assert result["components"][1]["totals"]["resources"] == 0


def test_reads_outputs_moves_imports_drift_and_deferred_are_separate():
    result = summarize_plan(
        _plan(
            _change("data.aws_ami.image", ["read"], mode="data"),
            _change(actions=["no-op"], previous_address="module.old.aws_instance.app"),
            _change(
                "aws_instance.imported",
                change={"actions": ["no-op"], "importing": {"id": "secret"}},
            ),
            output_changes={
                "endpoint": {"actions": ["update"]},
                "unchanged": {"actions": ["no-op"]},
            },
            resource_drift=[_change(actions=["update"])],
            deferred_changes=[
                {"reason": "instance_count_unknown", "resource_change": _change()}
            ],
        ),
        ["api"],
    )
    assert result["totals"]["resources"] == 2
    for category in ["read", "move", "import", "outputs", "drift", "deferred"]:
        assert result["totals"][category] == 1
    assert result["totals"]["create"] == result["totals"]["update"] == 0
    assert result["complete"] is False


def test_output_only_plan_is_preserved():
    result = summarize_plan(
        _plan(output_changes={"endpoint": {"actions": ["update"], "after": "private"}}),
        [],
    )
    assert result["totals"]["resources"] == 0
    assert result["totals"]["outputs"] == 1
    assert result["complete"] is True


def test_summary_excludes_values_and_unrecognized_fields():
    result = summarize_plan(
        _plan(
            _change(
                change={
                    "actions": ["update"],
                    "before": "secret-before",
                    "after": "secret-after",
                    "importing": {"id": "secret-import"},
                    "replace_paths": [["secret-path"]],
                    "after_sensitive": False,
                },
                generated_config="secret-config",
                future_field="secret-resource",
            ),
            variables={"password": {"value": "secret-variable"}},
            output_changes={
                "password": {"actions": ["update"], "after": "secret-output"}
            },
            checks=[{"message": "secret-diagnostic"}],
            future_field="secret-root",
        ),
        ["api"],
    )
    assert "secret" not in json.dumps(result)
    assert result["totals"]["import"] == 1


@pytest.mark.parametrize("version", [None, "", "2.0"])
def test_rejects_unsupported_plan_format(version):
    with pytest.raises(ValueError, match="format_version"):
        summarize_plan(_plan(format_version=version), [])


def test_report_is_complete_despite_policy_failure_and_matches_console(
    tmp_path, capsys
):
    path = tmp_path / "plan-summary.json"
    with plan_summary_session(
        True, path, {"prod": ["api"]}, "plan", "normal", "detailed"
    ) as report:
        report.start("prod")
        report.collect("prod", _plan(_change(actions=["delete", "create"])))
        report.finish("prod", 1)
        report.exit_code = 1
    payload = json.loads(path.read_text())
    assert payload["complete"] is True
    assert payload["exit_code"] == 1
    assert payload["stacks"][0]["policy_status"] == "failed"
    assert payload["totals"]["replace"] == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "module.api.aws_instance.app (replace)" in captured.err
    assert "Plan summary — complete" in captured.err
    assert active_plan_summary() is None


def test_validation_linkage_uses_shared_run_id_and_combined_footer(tmp_path, capsys):
    path = tmp_path / "plan-summary.json"
    validation_path = tmp_path / "validation-report.json"
    with plan_summary_session(
        True,
        path,
        {"prod": ["api"]},
        "plan",
        "normal",
        "table",
        validation_report_path=validation_path,
    ) as report:
        report.start("prod")
        report.collect("prod", _plan(_change()))
        report.finish("prod", 1)
        report.exit_code = 1
        report.attach_validation(
            {
                "status": "fail",
                "summary": {"pass": 1, "warn": 0, "fail": 1},
            }
        )

    payload = json.loads(path.read_text())
    assert payload["run_id"] == report.run_id
    assert payload["validation_status"] == "fail"
    assert payload["validation_summary"] == {"pass": 1, "warn": 0, "fail": 1}
    assert payload["validation_report"] == {
        "format": "json",
        "path": str(validation_path),
        "destination": "file",
    }
    captured = capsys.readouterr()
    assert "Validation: fail — 1 pass, 0 warn, 1 fail" in captured.err
    assert f"  Plan summary: {path}" in captured.err
    assert f"  Validation report: {validation_path}" in captured.err


def test_console_table_folds_long_stack_names_without_ellipsis(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("STACKSMITH_CONSOLE_WIDTH", "100")
    with plan_summary_session(
        True,
        tmp_path / "plan-summary.json",
        {"production-stack-with-an-intentionally-very-long-name": ["api"]},
        "plan",
        "normal",
    ) as report:
        stack_name = "production-stack-with-an-intentionally-very-long-name"
        report.start(stack_name)
        report.collect(stack_name, _plan(_change()))
        report.finish(stack_name, 0)
        report.exit_code = 0

    rendered = capsys.readouterr().err
    assert "…" not in rendered
    assert (
        len(next(line for line in rendered.splitlines() if line.startswith("┏"))) == 100
    )


def test_direct_plan_links_validation_to_stdout(tmp_path):
    with plan_summary_session(
        True, tmp_path / "plan-summary.json", {}, "plan", "normal", "none"
    ) as report:
        report.exit_code = 0
    assert report.payload()["validation_report"] == {
        "format": "json",
        "path": None,
        "destination": "stdout",
    }


def test_interrupted_report_marks_running_and_not_run_stacks(tmp_path):
    path = tmp_path / "report.json"
    with (
        pytest.raises(KeyboardInterrupt),
        plan_summary_session(
            True, path, {"a": [], "b": [], "c": []}, "run-all plan", "normal", "none"
        ) as report,
    ):
        report.start("a")
        report.collect("a", _plan())
        report.finish("a", 0)
        report.start("b")
        raise KeyboardInterrupt
    payload = json.loads(path.read_text())
    assert payload["complete"] is False
    assert [stack["status"] for stack in payload["stacks"]] == [
        "completed",
        "failed",
        "not_run",
    ]
    assert active_plan_summary() is None


def test_empty_selection_overwrites_stale_report_without_console(tmp_path, capsys):
    path = tmp_path / "report.json"
    path.write_text('{"stale": true}')
    with plan_summary_session(True, path, {}, "plan", "destroy", "none") as report:
        report.exit_code = 0
    payload = json.loads(path.read_text())
    assert payload["empty_selection"] is True
    assert payload["complete"] is True
    assert payload["stacks"] == []
    assert capsys.readouterr().err == ""
    assert list(tmp_path.iterdir()) == [path]


def test_non_plan_session_does_not_write(tmp_path):
    with plan_summary_session(False, tmp_path / "report.json", {}, "apply", "normal"):
        assert active_plan_summary() is None
    assert not list(tmp_path.iterdir())


def test_report_write_failure_propagates(tmp_path):
    path = tmp_path / "file"
    path.write_text("not a directory")
    with (
        pytest.raises(OSError),
        plan_summary_session(True, path / "report.json", {}, "plan", "normal"),
    ):
        pass


def _fake_tool_result(cmd, working_dir, auth_config=None):
    if "show" in cmd:
        return SimpleNamespace(
            returncode=0, stdout=json.dumps(_plan(_change())), stderr=""
        )
    return 0


def test_runner_collects_all_plans_after_fail_on_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_TOOL_VERSION_CHECKED", True)
    monkeypatch.setattr(runner, "_run_terragrunt_streaming", _fake_tool_result)
    monkeypatch.setattr(runner, "_run_terragrunt_capture_text", _fake_tool_result)
    with plan_summary_session(
        True,
        tmp_path / "report.json",
        {"a": ["api"], "b": ["api"]},
        "run-all plan",
        "normal",
        "none",
    ) as report:
        report.exit_code = runner.run_terragrunt_all_ordered(
            "plan",
            {"a": tmp_path, "b": tmp_path},
            fail_on_changes=True,
        )
    payload = json.loads((tmp_path / "report.json").read_text())
    assert payload["exit_code"] != 0
    assert payload["complete"] is True
    assert payload["totals"]["create"] == 2
    assert all(stack["status"] == "completed" for stack in payload["stacks"])
    assert not list(tmp_path.glob("*.plan"))


def test_runner_failure_stops_subsequent_plans(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_TOOL_VERSION_CHECKED", True)
    monkeypatch.setattr(runner, "_run_terragrunt_streaming", lambda *args, **kwargs: 1)
    with plan_summary_session(
        True,
        tmp_path / "report.json",
        {"a": [], "b": []},
        "run-all plan",
        "normal",
        "none",
    ) as report:
        report.exit_code = runner.run_terragrunt_all_ordered(
            "plan", {"a": tmp_path, "b": tmp_path}
        )
    payload = json.loads((tmp_path / "report.json").read_text())
    assert payload["exit_code"] == 1
    assert payload["complete"] is False
    assert [stack["status"] for stack in payload["stacks"]] == ["failed", "not_run"]


def test_detailed_exitcode_still_renders_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_TOOL_VERSION_CHECKED", True)
    monkeypatch.setattr(runner, "_run_terragrunt_streaming", lambda *args, **kwargs: 2)
    monkeypatch.setattr(runner, "_run_terragrunt_capture_text", _fake_tool_result)
    with plan_summary_session(
        True, tmp_path / "report.json", {"a": ["api"]}, "plan", "normal", "none"
    ) as report:
        report.exit_code = runner.run_terragrunt(
            ["plan", "-detailed-exitcode"], tmp_path, stack_name="a"
        )
    assert report.exit_code == 2
    assert report.payload()["stacks"][0]["policy_status"] == "passed"
    assert report.payload()["totals"]["create"] == 1


def test_destroy_plan_is_collected(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_TOOL_VERSION_CHECKED", True)
    monkeypatch.setattr(runner, "_run_terragrunt_streaming", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        runner,
        "_run_terragrunt_capture_text",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_plan(_change(actions=["delete"]))),
            stderr="",
        ),
    )
    with plan_summary_session(
        True, tmp_path / "report.json", {"a": ["api"]}, "plan", "destroy", "none"
    ) as report:
        report.exit_code = runner.run_terragrunt(
            ["plan", "-destroy"], tmp_path, stack_name="a"
        )
    assert report.exit_code == 0
    assert report.payload()["totals"]["destroy"] == 1


@pytest.mark.parametrize(
    "plan",
    [
        _plan(output_changes={"password": {"actions": {"after": "secret"}}}),
        _plan(_change(previous_address={"after": "secret"})),
        _plan(_change(change=None)),
        _plan(format_version=1),
    ],
)
def test_malformed_metadata_cannot_leak_values(plan):
    with pytest.raises((TypeError, ValueError)):
        summarize_plan(plan, ["api"])


def test_unknown_output_action_marks_report_incomplete():
    result = summarize_plan(
        _plan(output_changes={"endpoint": {"actions": ["future"]}}), []
    )
    assert result["complete"] is False
    assert result["outputs"][0]["action"] == "unknown"


def test_show_failure_with_detailed_exitcode_stops_subsequent_stacks(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(runner, "_TOOL_VERSION_CHECKED", True)
    monkeypatch.setattr(runner, "_run_terragrunt_streaming", lambda *args, **kwargs: 2)
    monkeypatch.setattr(
        runner,
        "_run_terragrunt_capture_text",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=2,
            stderr="show failed",
            stdout="",
        ),
    )
    with plan_summary_session(
        True,
        tmp_path / "report.json",
        {"a": [], "b": []},
        "run-all plan",
        "normal",
        "none",
    ) as report:
        report.exit_code = runner.run_terragrunt_all_ordered(
            ["plan", "-detailed-exitcode"],
            {"a": tmp_path, "b": tmp_path},
        )
    assert [stack["status"] for stack in report.payload()["stacks"]] == [
        "failed",
        "not_run",
    ]


def test_policy_failure_collects_subsequent_plans(tmp_path, monkeypatch):
    from stacksmith.validations import PlanValidationOutcome, PlanValidationResult

    monkeypatch.setattr(runner, "_TOOL_VERSION_CHECKED", True)
    monkeypatch.setattr(runner, "_run_terragrunt_streaming", _fake_tool_result)
    monkeypatch.setattr(runner, "_run_terragrunt_capture_text", _fake_tool_result)
    monkeypatch.setattr(
        runner,
        "check_plan_validations",
        lambda *args, **kwargs: [
            PlanValidationResult("deny", PlanValidationOutcome.FAIL, "Denied"),
        ],
    )
    with plan_summary_session(
        True,
        tmp_path / "report.json",
        {"a": ["api"], "b": ["api"]},
        "run-all plan",
        "normal",
        "none",
    ) as report:
        report.exit_code = runner.run_terragrunt_all_ordered(
            "plan",
            {"a": tmp_path, "b": tmp_path},
            config=SimpleNamespace(
                plan_validations={"deny": SimpleNamespace(enabled=True)}
            ),
        )
    assert report.exit_code == 1
    assert report.payload()["complete"] is True
    assert report.payload()["totals"]["create"] == 2
