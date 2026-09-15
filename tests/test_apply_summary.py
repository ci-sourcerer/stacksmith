import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from stacksmith import runner
from stacksmith.apply_summary import (
    ApplySummary,
    active_apply_summary,
    apply_summary_session,
)


def _event(kind, action="create", address="module.api.aws_instance.web", **fields):
    event = {"type": kind, **fields}
    if kind in {"planned_change", "apply_start", "apply_complete", "apply_errored"}:
        event["change" if kind == "planned_change" else "hook"] = {
            "resource": {"addr": address, "resource_type": "aws_instance"},
            "action": action,
        }
    return event


def _begin(report, name="prod"):
    report.start(name)
    report.collect(name, {"type": "version", "ui": "1.0"})


def _complete(report, name="prod", exit_code=0):
    if exit_code == 0:
        report.collect(
            name, {"type": "change_summary", "changes": {"operation": "apply"}}
        )
    report.finish(name, exit_code)
    report.exit_code = exit_code


@pytest.mark.parametrize(
    "action,category",
    [
        ("create", "create"),
        ("update", "update"),
        ("delete", "destroy"),
        ("read", "read"),
        ("forget", "forget"),
    ],
)
def test_only_counts_completed_operations(action, category):
    report = ApplySummary({"prod": ["api"]}, "apply")
    _begin(report)
    report.collect("prod", _event("planned_change", action))
    assert report.payload()["totals"][category] == 0
    report.collect("prod", _event("apply_start", action))
    assert report.payload()["totals"][category] == 0
    report.collect("prod", _event("apply_complete", action))
    _complete(report)
    assert report.payload()["totals"][category] == 1
    assert report.payload()["totals"]["resources"] == (0 if action == "read" else 1)
    assert report.payload()["complete"] is True
    assert report.payload()["run_id"] == report.run_id


@pytest.mark.parametrize("order", [("delete", "create"), ("create", "delete")])
def test_replacement_requires_both_completed_steps(order):
    report = ApplySummary({"prod": ["api"]}, "apply")
    _begin(report)
    report.collect("prod", _event("planned_change", "replace"))
    for action in order:
        report.collect("prod", _event("apply_start", action))
        report.collect("prod", _event("apply_complete", action))
    _complete(report)
    assert report.payload()["totals"]["replace"] == 1
    assert report.payload()["totals"]["resources"] == 1
    assert (
        report.payload()["totals"]["create"]
        == report.payload()["totals"]["destroy"]
        == 0
    )
    assert report.payload()["complete"] is True


def test_partial_replacement_reports_successful_deletion_and_failed_creation():
    report = ApplySummary({"prod": ["api"]}, "apply")
    _begin(report)
    report.collect("prod", _event("planned_change", "replace"))
    report.collect("prod", _event("apply_start", "delete"))
    report.collect("prod", _event("apply_complete", "delete"))
    report.collect("prod", _event("apply_start", "create"))
    report.collect("prod", _event("apply_errored", "create"))
    _complete(report, exit_code=1)
    payload = report.payload()
    assert payload["totals"]["destroy"] == 1
    assert payload["totals"]["replace"] == payload["totals"]["create"] == 0
    assert payload["totals"]["failed"] == 1
    assert payload["stacks"][0]["resources"][0]["status"] == "partial"
    assert payload["complete"] is False


def test_interleaved_events_map_nested_and_root_resources():
    report = ApplySummary({"prod": ["api", "idle"]}, "apply")
    _begin(report)
    report.collect(
        "prod",
        _event(
            "apply_start", address='module.api["blue"].module.child.aws_instance.web'
        ),
    )
    report.collect("prod", _event("apply_start", "update", "aws_instance.root"))
    report.collect("prod", _event("apply_complete", "update", "aws_instance.root"))
    report.collect(
        "prod",
        _event(
            "apply_complete", address='module.api["blue"].module.child.aws_instance.web'
        ),
    )
    _complete(report)
    payload = report.payload()
    assert payload["totals"]["create"] == payload["totals"]["update"] == 1
    assert [
        resource["component"] for resource in payload["stacks"][0]["resources"]
    ] == [None, "api"]
    assert [
        component["name"] for component in payload["stacks"][0]["component_totals"]
    ] == ["api", "idle", None]


def test_unconfirmed_state_only_changes_are_not_counted_as_completed():
    report = ApplySummary({"prod": ["api"]}, "apply")
    _begin(report)
    report.collect("prod", _event("planned_change", "move"))
    _complete(report, exit_code=1)
    assert report.payload()["totals"]["move"] == 0
    assert report.payload()["totals"]["unconfirmed"] == 1
    assert report.payload()["complete"] is False


def test_raw_values_ids_and_diagnostics_are_never_archived():
    report = ApplySummary({"prod": ["api"]}, "apply")
    _begin(report)
    report.collect("prod", _event("apply_start"))
    event = _event(
        "apply_complete", **{"@message": "secret-message", "future": "secret-field"}
    )
    event["hook"].update(
        id_value="secret-id", before="secret-before", after="secret-after"
    )
    report.collect("prod", event)
    report.collect(
        "prod", {"type": "diagnostic", "diagnostic": {"detail": "secret-diagnostic"}}
    )
    report.collect(
        "prod",
        {
            "type": "outputs",
            "outputs": {"password": {"sensitive": True, "value": "secret-password"}},
        },
    )
    _complete(report)
    assert "secret" not in json.dumps(report.payload())
    assert report.payload()["stacks"][0]["output_names"] == ["password"]
    assert report.payload()["output_changes_available"] is False


@pytest.mark.parametrize(
    "record", ['{"type":', '{"type":"version","ui":"2.0"}', '{"type":"new_event"}']
)
def test_invalid_events_leave_an_explicitly_incomplete_report(tmp_path, record):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"type": "version", "ui": "1.0"}) + "\n" + record + "\n")
    report = ApplySummary({"prod": ["api"]}, "apply")
    report.start("prod")
    report.collect_file("prod", path)
    report.finish("prod", 0)
    assert report.payload()["complete"] is False
    assert report.payload()["stacks"][0]["issues"]


def test_missing_events_never_means_no_changes():
    report = ApplySummary({"prod": []}, "apply")
    report.start("prod")
    report.finish("prod", 0)
    assert report.payload()["complete"] is False


def test_completion_without_start_preserves_confirmed_action_but_flags_coverage():
    report = ApplySummary({"prod": ["api"]}, "apply")
    _begin(report)
    report.collect("prod", _event("apply_complete"))
    _complete(report)
    assert report.payload()["totals"]["create"] == 1
    assert report.payload()["complete"] is False


def test_interrupt_writes_partial_artifact_and_restores_context(tmp_path):
    path = tmp_path / "apply-summary.json"
    with (
        pytest.raises(KeyboardInterrupt),
        apply_summary_session(
            True, path, {"prod": ["api"], "shared": []}, "run-all apply", "none"
        ) as report,
    ):
        _begin(report)
        report.collect("prod", _event("apply_start"))
        report.collect("prod", _event("apply_complete"))
        raise KeyboardInterrupt
    payload = json.loads(path.read_text())
    assert payload["exit_code"] == 130
    assert payload["totals"]["create"] == 1
    assert [stack["status"] for stack in payload["stacks"]] == [
        "interrupted",
        "not_run",
    ]
    assert active_apply_summary() is None


def test_console_and_json_share_counts_and_stdout_is_preserved(tmp_path, capsys):
    with apply_summary_session(
        True, tmp_path / "report.json", {"prod": ["api"]}, "apply", "detailed"
    ) as report:
        _begin(report)
        report.collect("prod", _event("apply_start"))
        report.collect("prod", _event("apply_complete"))
        _complete(report)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Applied changes — complete" in captured.err
    assert "module.api.aws_instance.web" in captured.err
    assert "create: completed" in captured.err
    assert json.loads((tmp_path / "report.json").read_text())["totals"]["create"] == 1


def _write_successful_events(cmd, working_dir, auth_config=None):
    Path(
        next(
            argument.split("=", 1)[1]
            for argument in cmd
            if argument.startswith("-json-into=")
        )
    ).write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "version", "ui": "1.0"},
                _event("apply_start"),
                _event("apply_complete"),
                {"type": "change_summary", "changes": {"operation": "apply"}},
            ]
        )
    )
    return 0


@pytest.mark.parametrize("action", ["apply", "destroy"])
def test_runner_captures_completed_events_and_removes_raw_files(
    tmp_path, monkeypatch, action
):
    monkeypatch.setattr(runner, "_TOOL_VERSION_CHECKED", True)
    monkeypatch.setattr(runner, "_supports_json_event_file", lambda executable: True)
    commands = []

    def capture(cmd, working_dir, auth_config=None):
        commands.append(cmd)
        return _write_successful_events(cmd, working_dir, auth_config)

    monkeypatch.setattr(runner, "_run_terragrunt_streaming", capture)
    with apply_summary_session(
        True, tmp_path / "report.json", {"prod": ["api"]}, action, "none"
    ) as report:
        report.exit_code = runner.run_terragrunt([action], tmp_path, stack_name="prod")
    assert report.payload()["complete"] is True
    assert report.payload()["totals"]["create"] == 1
    assert "--auto-approve" not in commands[0]
    assert not Path(
        next(
            argument.split("=", 1)[1]
            for argument in commands[0]
            if argument.startswith("-json-into=")
        )
    ).exists()


def test_unsupported_capture_preserves_command_and_reports_unavailable(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(runner, "_TOOL_VERSION_CHECKED", True)
    monkeypatch.setattr(runner, "_supports_json_event_file", lambda executable: False)
    commands = []

    def capture(cmd, working_dir, auth_config=None):
        commands.append(cmd)
        return 0

    monkeypatch.setattr(runner, "_run_terragrunt_streaming", capture)
    with apply_summary_session(
        True, tmp_path / "report.json", {"prod": []}, "apply", "none"
    ) as report:
        report.exit_code = runner.run_terragrunt(["apply"], tmp_path, stack_name="prod")
    assert "-json-into" not in " ".join(commands[0])
    assert "--auto-approve" not in commands[0]
    assert report.payload()["stacks"][0]["issues"] == ["json_event_capture_unavailable"]
    assert report.payload()["complete"] is False


def test_run_all_apply_stops_at_failure_and_retains_completed_changes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(runner, "_TOOL_VERSION_CHECKED", True)
    monkeypatch.setattr(runner, "_supports_json_event_file", lambda executable: True)

    def fail(cmd, working_dir, auth_config=None):
        _write_successful_events(cmd, working_dir, auth_config)
        return 1

    monkeypatch.setattr(runner, "_run_terragrunt_streaming", fail)
    with apply_summary_session(
        True, tmp_path / "report.json", {"a": ["api"], "b": []}, "run-all apply", "none"
    ) as report:
        report.exit_code = runner.run_terragrunt_all_ordered(
            "apply", {"a": tmp_path, "b": tmp_path}
        )
    assert report.exit_code == 1
    assert report.payload()["totals"]["create"] == 1
    assert [stack["status"] for stack in report.payload()["stacks"]] == [
        "failed",
        "not_run",
    ]


def test_capability_probe_is_read_only(monkeypatch):
    runner._supports_json_event_file.cache_clear()
    calls = []

    def probe(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout="  -json-into=path")

    monkeypatch.setattr(runner.subprocess, "run", probe)
    assert runner._supports_json_event_file("/test/tofu") is True
    assert runner._supports_json_event_file("/test/tofu") is True
    assert len(calls) == 1
    assert calls[0][0] == ["/test/tofu", "apply", "-help"]
    assert calls[0][1]["shell"] is False
    runner._supports_json_event_file.cache_clear()


@pytest.mark.parametrize(
    "action,category", [("import", "import"), ("remove", "forget"), ("move", "move")]
)
def test_successful_apply_confirms_state_only_actions(action, category):
    report = ApplySummary({"prod": ["api"]}, "apply")
    _begin(report)
    report.collect("prod", _event("planned_change", action))
    report.collect(
        "prod",
        {
            "type": "change_summary",
            "changes": {
                "operation": "apply",
                "import": int(category == "import"),
                "forget": int(category == "forget"),
            },
        },
    )
    report.finish("prod", 0)
    assert report.payload()["totals"][category] == 1
    assert report.payload()["complete"] is True
    assert report.payload()["stacks"][0]["resources"][0]["operations"][0][
        "completion_source"
    ] in {"apply_summary", "successful_apply"}


def test_import_counter_mismatch_does_not_confirm_individual_resources():
    report = ApplySummary({"prod": ["api"]}, "apply")
    _begin(report)
    report.collect("prod", _event("planned_change", "import"))
    report.collect(
        "prod",
        {"type": "change_summary", "changes": {"operation": "apply", "import": 0}},
    )
    report.finish("prod", 0)
    assert report.payload()["totals"]["import"] == 0
    assert report.payload()["complete"] is False


@pytest.mark.parametrize(
    "event",
    [
        {"type": "change_summary", "changes": None},
        {"type": "apply_start", "hook": []},
        {
            "type": "change_summary",
            "changes": {"operation": "apply", "import": {"value": "secret"}},
        },
    ],
)
def test_invalid_nested_metadata_is_not_archived(tmp_path, event):
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({"type": "version", "ui": "1.0"}) + "\n" + json.dumps(event)
    )
    report = ApplySummary({"prod": []}, "apply")
    report.start("prod")
    report.collect_file("prod", path)
    assert report.payload()["stacks"][0]["issues"] == ["invalid_event"]
    assert "secret" not in json.dumps(report.payload())


def test_final_totals_detect_missing_resource_events():
    report = ApplySummary({"prod": []}, "apply")
    _begin(report)
    report.collect(
        "prod", {"type": "change_summary", "changes": {"operation": "apply", "add": 1}}
    )
    report.finish("prod", 0)
    assert report.payload()["complete"] is False
    assert report.payload()["stacks"][0]["issues"] == ["unmatched_apply_totals"]
    assert report.payload()["stacks"][0]["reported_totals"]["add"] == 1


def test_duplicate_completion_is_not_counted_twice():
    report = ApplySummary({"prod": ["api"]}, "apply")
    _begin(report)
    report.collect("prod", _event("apply_start"))
    report.collect("prod", _event("apply_complete"))
    report.collect("prod", _event("apply_complete"))
    _complete(report)
    assert report.payload()["totals"]["create"] == 1
    assert report.payload()["complete"] is False


def test_saved_plan_arguments_remain_after_event_capture_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_TOOL_VERSION_CHECKED", True)
    monkeypatch.setattr(runner, "_supports_json_event_file", lambda executable: True)
    commands = []

    def capture(cmd, working_dir, auth_config=None):
        commands.append(cmd)
        return _write_successful_events(cmd, working_dir, auth_config)

    monkeypatch.setattr(runner, "_run_terragrunt_streaming", capture)
    with apply_summary_session(
        True, tmp_path / "report.json", {"prod": ["api"]}, "apply", "none"
    ) as report:
        report.exit_code = runner.run_terragrunt(
            ["apply", str(tmp_path / "saved.plan")], tmp_path, stack_name="prod"
        )
    assert commands[0][-1] == str(tmp_path / "saved.plan")
    assert commands[0][commands[0].index("apply") + 1].startswith("-json-into=")
    assert "--auto-approve" not in commands[0]


def test_empty_selection_replaces_old_report_and_none_suppresses_console(
    tmp_path, capsys
):
    path = tmp_path / "report.json"
    path.write_text('{"stale": true}')
    with apply_summary_session(True, path, {}, "run-all apply", "none") as report:
        report.exit_code = 0
    payload = json.loads(path.read_text())
    assert payload["empty_selection"] is True
    assert payload["complete"] is True
    assert capsys.readouterr().err == ""


def test_report_write_errors_propagate(tmp_path):
    (tmp_path / "file").write_text("existing")
    with (
        pytest.raises(OSError),
        apply_summary_session(True, tmp_path / "file" / "report.json", {}, "apply"),
    ):
        pass
