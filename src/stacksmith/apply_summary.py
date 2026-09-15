import json
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from rich.table import Table

from .change_reports import (
    change_report_console,
    component_for_address,
    write_change_report,
)

_ACTIVE_REPORT: ContextVar[ApplySummary | None] = ContextVar(
    "apply_summary", default=None
)
_ACTIONS = {
    "create",
    "update",
    "delete",
    "replace",
    "read",
    "noop",
    "forget",
    "import",
    "move",
}
_TOTAL_KEYS = (
    "create",
    "update",
    "replace",
    "destroy",
    "read",
    "forget",
    "import",
    "move",
    "resources",
    "failed",
    "incomplete",
    "unconfirmed",
)


def _string(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("Apply event metadata must be a string.")
    return value


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("Apply event records must be objects.")
    return value


def _action(value: Any) -> str:
    action = _string(value)
    if action == "remove":
        return "forget"
    if action not in _ACTIONS:
        raise ValueError("Unsupported apply action.")
    return action


def _pending_state_actions(resource: dict[str, Any]) -> set[str]:
    expected = {
        action
        for action in ("import", "move", "forget")
        if resource[f"planned_{action}"]
    }
    return expected - {
        operation["action"]
        for operation in resource["operations"]
        if operation["status"] == "completed"
    }


def _resource_status(resource: dict[str, Any]) -> str:
    states = {operation["status"] for operation in resource["operations"]}
    if "failed" in states:
        return "partial" if "completed" in states else "failed"
    if "started" in states:
        return "partial" if "completed" in states else "incomplete"
    if not states:
        return "unconfirmed"
    if _pending_state_actions(resource):
        return "partial"
    if resource["planned_action"] == "replace" and not (
        {"create", "delete"}
        <= {operation["action"] for operation in resource["operations"]}
        or any(operation["action"] == "replace" for operation in resource["operations"])
    ):
        return "partial"
    return "completed"


def _resource_totals(resources: Sequence[dict[str, Any]]) -> dict[str, int]:
    totals = Counter({key: 0 for key in _TOTAL_KEYS})
    for resource in resources:
        completed = Counter(
            operation["action"]
            for operation in resource["operations"]
            if operation["status"] == "completed"
        )
        replacements = (
            min(completed["create"], completed["delete"])
            if resource["planned_action"] == "replace"
            else 0
        )
        totals["replace"] += completed["replace"] + replacements
        totals["create"] += completed["create"] - replacements
        totals["destroy"] += completed["delete"] - replacements
        for action in ("update", "read", "forget", "import", "move"):
            totals[action] += completed[action]
        totals["resources"] += int(
            any(completed[action] for action in _ACTIONS - {"noop", "read"})
        )
        totals["failed"] += int(
            any(operation["status"] == "failed" for operation in resource["operations"])
        )
        totals["incomplete"] += int(
            _resource_status(resource) in {"partial", "incomplete"}
        )
        totals["unconfirmed"] += int(_resource_status(resource) == "unconfirmed")
    return dict(totals)


class ApplySummary:
    """Collect confirmed resource operations from OpenTofu's JSON UI events.

    Attributes:
        stacks: Selected stack coverage and value-free event inventories.
        exit_code: Infrastructure execution result.
        run_id: Unique identifier shared with the containing CI report.
    """

    def __init__(
        self,
        stacks: Mapping[str, Sequence[str]],
        command: str,
        selection: Mapping[str, Any] | None = None,
    ) -> None:
        self.stacks = {
            name: {
                "name": name,
                "components": sorted(components),
                "status": "not_run",
                "exit_code": None,
                "resources": {},
                "output_names": [],
                "stream_complete": False,
                "reported_totals": {},
                "ui_version": None,
                "issues": [],
            }
            for name, components in stacks.items()
        }
        self.command = command
        self.selection = dict(selection or {})
        self.started_at = datetime.now(UTC).isoformat()
        self.exit_code = 1
        self.run_id = str(uuid4())

    def start(self, name: str) -> None:
        """Mark the stack as executing.

        Args:
            name: Stack identifier.
        """
        self.stacks[name]["status"] = "running"

    def unavailable(self, name: str) -> None:
        """Record unavailable event capture without guessing resource outcomes.

        Args:
            name: Stack identifier.
        """
        self.stacks[name]["issues"].append("json_event_capture_unavailable")

    def collect_file(self, name: str, path: Path) -> None:
        """Read event records, retaining valid results from a damaged stream.

        Args:
            name: Stack identifier.
            path: Temporary raw JSON event file.

        Raises:
            OSError: If the event file cannot be read.
        """
        with path.open("rb") as events:
            for line in events:
                if not line.strip():
                    continue
                try:
                    self.collect(name, json.loads(line))
                except json.JSONDecodeError, TypeError, ValueError, KeyError:
                    self.stacks[name]["issues"].append("invalid_event")

    def collect(self, name: str, event: dict[str, Any]) -> None:
        """Process an event using an allowlist of identity and action fields.

        Args:
            name: Stack identifier.
            event: Parsed JSON UI event.

        Raises:
            TypeError: If event metadata has invalid types.
            ValueError: If an action or UI version is unsupported.
            KeyError: If required metadata is missing.
        """
        if not isinstance(event, dict):
            raise TypeError("Apply events must be objects.")
        stack = self.stacks[name]
        kind = _string(event["type"])
        if kind == "version":
            if stack["ui_version"] is not None:
                stack["issues"].append("multiple_event_streams")
            stack["ui_version"] = _string(event["ui"])
            if stack["ui_version"].split(".")[0] != "1":
                raise ValueError("Unsupported OpenTofu JSON UI version.")
            return
        if stack["ui_version"] is None or stack["ui_version"].split(".")[0] != "1":
            stack["issues"].append("missing_or_unsupported_ui_version")
            return
        if kind == "planned_change":
            self._planned_change(stack, event["change"])
        elif kind in {"apply_start", "apply_complete", "apply_errored"}:
            self._operation(stack, event["hook"], kind)
        elif kind == "change_summary":
            self._change_summary(stack, _object(event["changes"]))
        elif kind == "outputs":
            if not isinstance(event["outputs"], dict):
                raise TypeError("Outputs must be an object.")
            stack["output_names"] = sorted(_string(key) for key in event["outputs"])
        elif kind not in {
            "change_summary",
            "diagnostic",
            "log",
            "apply_progress",
            "provision_start",
            "provision_progress",
            "provision_complete",
            "provision_errored",
            "refresh_start",
            "refresh_complete",
            "resource_drift",
        }:
            stack["issues"].append("unknown_event_type")

    def _resource(
        self, stack: dict[str, Any], resource: dict[str, Any]
    ) -> dict[str, Any]:
        resource = _object(resource)
        address = _string(resource["addr"])
        if address not in stack["resources"]:
            stack["resources"][address] = {
                "address": address,
                "component": component_for_address(address, stack["components"]),
                "type": _string(resource["resource_type"]),
                "planned_action": None,
                "planned_import": False,
                "planned_move": False,
                "planned_forget": False,
                "previous_address": None,
                "operations": [],
            }
        return stack["resources"][address]

    def _planned_change(self, stack: dict[str, Any], change: dict[str, Any]) -> None:
        change = _object(change)
        action = _action(change["action"])
        if (
            action == "noop"
            and not change.get("importing")
            and not change.get("previous_resource")
        ):
            return
        resource = self._resource(stack, change["resource"])
        resource["planned_action"] = action
        resource["planned_import"] = (
            action == "import" or change.get("importing") is not None
        )
        resource["planned_move"] = (
            action == "move" or change.get("previous_resource") is not None
        )
        resource["planned_forget"] = action == "forget"
        if change.get("previous_resource") is not None:
            resource["previous_address"] = _string(
                _object(change["previous_resource"])["addr"]
            )

    def _change_summary(self, stack: dict[str, Any], changes: dict[str, Any]) -> None:
        if changes.get("operation") not in {"apply", "destroy"}:
            return
        totals = {}
        for field in ("add", "change", "remove", "import", "forget"):
            if field in changes:
                if type(changes[field]) is not int or changes[field] < 0:
                    raise ValueError(
                        "Apply summary counts must be non-negative integers."
                    )
                totals[field] = changes[field]
        stack["reported_totals"] = totals
        stack["stream_complete"] = True

    def _confirm_state_actions(self, stack: dict[str, Any]) -> None:
        # State-only operations have no per-resource completion hook. Successful
        # execution and matching final counters confirm imports and removals.
        for action in ("import", "move", "forget"):
            resources = [
                resource
                for resource in stack["resources"].values()
                if resource[f"planned_{action}"]
            ]
            if action != "move" and stack["reported_totals"].get(action) != len(
                resources
            ):
                continue
            for resource in resources:
                if action in _pending_state_actions(resource):
                    resource["operations"].append(
                        {
                            "action": action,
                            "status": "completed",
                            "completion_source": "successful_apply"
                            if action == "move"
                            else "apply_summary",
                        }
                    )

    def _operation(
        self, stack: dict[str, Any], hook: dict[str, Any], kind: str
    ) -> None:
        hook = _object(hook)
        action = _action(hook["action"])
        operations = self._resource(stack, hook["resource"])["operations"]
        if kind == "apply_start":
            operations.append({"action": action, "status": "started"})
            return
        for operation in operations:
            if operation["action"] == action and operation["status"] == "started":
                operation["completion_source"] = "resource_hook"
                operation["status"] = (
                    "completed" if kind == "apply_complete" else "failed"
                )
                return
        if any(
            operation["action"] == action
            and operation["status"]
            == ("completed" if kind == "apply_complete" else "failed")
            for operation in operations
        ):
            stack["issues"].append("duplicate_completion")
            return
        stack["issues"].append("completion_without_start")
        operations.append(
            {
                "action": action,
                "completion_source": "resource_hook",
                "status": "completed" if kind == "apply_complete" else "failed",
            }
        )

    def _verify_totals(self, stack: dict[str, Any]) -> None:
        totals = _resource_totals(list(stack["resources"].values()))
        expected = {
            "add": totals["create"] + totals["replace"],
            "change": totals["update"],
            "remove": totals["destroy"] + totals["replace"],
            "import": totals["import"],
            "forget": totals["forget"],
        }
        if any(
            expected[field] != count
            for field, count in stack["reported_totals"].items()
        ):
            stack["issues"].append("unmatched_apply_totals")

    def finish(self, name: str, exit_code: int) -> None:
        """Record the command outcome independently of captured completions.

        Args:
            name: Stack identifier.
            exit_code: Underlying command's exit code.
        """
        if (
            exit_code == 0
            and self.stacks[name]["stream_complete"]
            and not self.stacks[name]["issues"]
        ):
            self._confirm_state_actions(self.stacks[name])
            self._verify_totals(self.stacks[name])
        self.stacks[name].update(
            status="completed" if exit_code == 0 else "failed", exit_code=int(exit_code)
        )

    def _stack_payload(self, stack: dict[str, Any]) -> dict[str, Any]:
        resources = [
            {**resource, "status": _resource_status(resource)}
            for _, resource in sorted(stack["resources"].items())
        ]
        return {
            **stack,
            "status": "interrupted"
            if stack["status"] == "running"
            else stack["status"],
            "issues": sorted(set(stack["issues"])),
            "resources": resources,
            "totals": _resource_totals(resources),
            "component_totals": [
                {
                    "name": component,
                    "totals": _resource_totals(
                        [
                            resource
                            for resource in resources
                            if resource["component"] == component
                        ]
                    ),
                }
                for component in [
                    *stack["components"],
                    *(
                        [None]
                        if any(resource["component"] is None for resource in resources)
                        else []
                    ),
                ]
            ],
            "complete": stack["status"] == "completed"
            and stack["stream_complete"]
            and not stack["issues"]
            and all(resource["status"] == "completed" for resource in resources),
        }

    def payload(self) -> dict[str, Any]:
        """Build the aggregate report from confirmed operations.

        Returns:
            Versioned JSON-compatible apply report.
        """
        stacks = [
            self._stack_payload(stack) for _, stack in sorted(self.stacks.items())
        ]
        totals = Counter({key: 0 for key in _TOTAL_KEYS})
        for stack in stacks:
            totals.update(stack["totals"])
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "command": self.command,
            "selection": self.selection,
            "scope": "infrastructure",
            "started_at": self.started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "exit_code": int(self.exit_code),
            "complete": all(stack["complete"] for stack in stacks),
            "empty_selection": not stacks,
            "output_changes_available": False,
            "totals": dict(totals),
            "stacks": stacks,
        }


def _render_report(payload: dict[str, Any], path: Path, detail: str) -> None:
    console = change_report_console()
    console.print(
        f"Applied changes — {'complete' if payload['complete'] else 'INCOMPLETE'}"
    )
    console.print("Counts include confirmed resource operations.")
    if payload["exit_code"] != 0:
        console.print(
            "Failed operations may have additional effects that could not be confirmed."
        )
    table = Table(expand=True)
    table.add_column("Stack", min_width=18, ratio=3, overflow="fold")
    table.add_column("Component", min_width=18, ratio=3, overflow="fold")
    for heading in ("Created", "Updated", "Replaced", "Destroyed", "Failed"):
        table.add_column(heading, justify="right", no_wrap=True)
    for stack in payload["stacks"]:
        if "json_event_capture_unavailable" in stack["issues"]:
            console.print(
                f"{stack['name']}: event capture unavailable; OpenTofu 1.12 or newer is required."
            )
        elif stack["status"] == "not_run" or stack["issues"]:
            console.print(
                f"{stack['name']}: {stack['status']} ({', '.join(stack['issues'])})"
            )
        for component in stack["component_totals"]:
            table.add_row(
                stack["name"],
                component["name"] or "(root/unmapped)",
                *(
                    str(component["totals"][key])
                    for key in ("create", "update", "replace", "destroy", "failed")
                ),
            )
    table.add_row(
        "TOTAL",
        "",
        *(
            str(payload["totals"][key])
            for key in ("create", "update", "replace", "destroy", "failed")
        ),
    )
    console.print(table)
    console.print(
        " · ".join(
            f"{value} {key}"
            for key, value in payload["totals"].items()
            if key not in {"create", "update", "replace", "destroy", "failed"}
        )
    )
    if detail == "detailed":
        for stack in payload["stacks"]:
            for resource in stack["resources"]:
                console.print(
                    f"  {stack['name']} / {resource['component'] or '(root/unmapped)'}: {resource['address']} — {resource['status']}"
                )
                for operation in resource["operations"]:
                    console.print(f"    {operation['action']}: {operation['status']}")
    console.print(f"Report: {path}")


def active_apply_summary() -> ApplySummary | None:
    """Return the report for the active infrastructure execution.

    Returns:
        Active report, or `None` when apply reporting is disabled.
    """
    return _ACTIVE_REPORT.get()


@contextmanager
def apply_summary_session(
    enabled: bool,
    path: Path,
    stacks: Mapping[str, Sequence[str]],
    command: str,
    detail: str = "table",
    selection: Mapping[str, Any] | None = None,
) -> Iterator[ApplySummary]:
    """Publish an apply report on success, failure, or interruption.

    Args:
        enabled: Whether the invocation changes infrastructure.
        path: Aggregate report destination.
        stacks: Selected stacks and known component names.
        command: Stacksmith action name.
        detail: Console format: `table`, `detailed`, or `none`.
        selection: Filters and targets for this invocation.

    Yields:
        Mutable report whose exit code is set by the caller.

    Raises:
        OSError: If the report cannot be written.
    """
    report = ApplySummary(stacks, command, selection)
    token = _ACTIVE_REPORT.set(report if enabled else None)
    try:
        yield report
    except KeyboardInterrupt:
        report.exit_code = 130
        raise
    finally:
        _ACTIVE_REPORT.reset(token)
        if enabled:
            payload = report.payload()
            write_change_report(payload, path)
            if detail != "none":
                _render_report(payload, path, detail)
