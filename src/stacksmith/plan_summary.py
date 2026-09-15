from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from rich.console import Console
from rich.table import Table

from .change_reports import component_for_address, write_change_report

_ACTIONS = {
    ("no-op",): "no_op",
    ("create",): "create",
    ("update",): "update",
    ("delete",): "destroy",
    ("read",): "read",
    ("delete", "create"): "replace",
    ("create", "delete"): "replace",
    ("forget",): "forget",
}
_COUNT_KEYS = (
    "create",
    "update",
    "replace",
    "destroy",
    "read",
    "forget",
    "unknown",
    "import",
    "move",
    "resources",
    "outputs",
    "drift",
    "deferred",
)
_ACTIVE_REPORT: ContextVar[PlanSummary | None] = ContextVar(
    "plan_summary", default=None
)


def _change_actions(change: dict[str, Any]) -> list[str]:
    if not isinstance(change, dict):
        raise TypeError("Plan changes must be objects.")
    actions = change.get("actions", [])
    if not isinstance(actions, list) or not all(
        isinstance(item, str) for item in actions
    ):
        raise ValueError("Plan actions must be an array of strings.")
    return actions


def _metadata_string(
    change: dict[str, Any], field: str, default: str | None = None
) -> str | None:
    value = change.get(field, default)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"Plan resource {field} must be a string.")
    return value


def _resource(change: dict[str, Any], components: Sequence[str]) -> dict[str, Any]:
    if not isinstance(change, dict):
        raise TypeError("Plan resource changes must be objects.")
    actions = _change_actions(change.get("change", {}))
    address = change.get("address")
    if not isinstance(address, str):
        raise TypeError("Plan resource address must be a string.")
    return {
        "address": address,
        "component": component_for_address(address, components),
        "type": _metadata_string(change, "type"),
        "mode": _metadata_string(change, "mode", "managed"),
        "actions": actions,
        "action": _ACTIONS.get(tuple(actions), "unknown"),
        "previous_address": _metadata_string(change, "previous_address"),
        "import": change.get("change", {}).get("importing") is not None,
    }


def _output_changes(changes: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(changes, dict):
        raise TypeError("Plan output changes must be an object.")
    outputs = []
    for name, change in sorted(changes.items()):
        actions = _change_actions(change)
        if actions != ["no-op"]:
            outputs.append(
                {
                    "name": name,
                    "actions": actions,
                    "action": _ACTIONS.get(tuple(actions), "unknown"),
                }
            )
    return outputs


def _changed(resource: dict[str, Any]) -> bool:
    return (
        resource["action"] != "no_op"
        or resource["import"]
        or resource["previous_address"] is not None
    )


def _counts(resources: Sequence[dict[str, Any]]) -> dict[str, int]:
    counts = Counter({key: 0 for key in _COUNT_KEYS})
    for resource in resources:
        if resource["action"] != "no_op":
            counts[resource["action"]] += 1
        counts["import"] += int(resource["import"])
        counts["move"] += int(resource["previous_address"] is not None)
        counts["resources"] += int(resource["mode"] == "managed" and _changed(resource))
    return dict(counts)


def summarize_plan(plan: dict[str, Any], components: Sequence[str]) -> dict[str, Any]:
    """Extract a value-free inventory of planned changes.

    Args:
        plan: OpenTofu's rendered JSON plan.
        components: Known top-level Stacksmith component names.

    Returns:
        Resource, component, output, drift and deferred change inventories.

    Raises:
        ValueError: If the plan format or action metadata is unsupported or invalid.
        TypeError: If the plan structure is invalid.
    """
    if not isinstance(plan, dict):
        raise TypeError("Plan JSON must be an object.")
    if (
        not isinstance(plan.get("format_version"), str)
        or plan["format_version"].split(".")[0] != "1"
    ):
        raise ValueError("Unsupported or missing OpenTofu plan format_version.")
    resources = sorted(
        (
            resource
            for change in plan.get("resource_changes", [])
            if _changed(resource := _resource(change, components))
        ),
        key=lambda resource: resource["address"],
    )
    outputs = _output_changes(plan.get("output_changes", {}))
    drift = sorted(
        [_resource(change, components) for change in plan.get("resource_drift", [])],
        key=lambda resource: resource["address"],
    )
    deferred = sorted(
        [
            _resource(change["resource_change"], components)
            for change in plan.get("deferred_changes", [])
        ],
        key=lambda resource: resource["address"],
    )
    totals = _counts(resources)
    totals.update(outputs=len(outputs), drift=len(drift), deferred=len(deferred))
    return {
        "complete": plan.get("complete", True) is not False
        and not plan.get("errored", False)
        and not deferred
        and not totals["unknown"]
        and all(output["action"] != "unknown" for output in outputs),
        "totals": totals,
        "resources": resources,
        "outputs": outputs,
        "drift": drift,
        "deferred": deferred,
        "components": [
            {
                "name": name,
                "totals": _counts(
                    [
                        resource
                        for resource in resources
                        if resource["component"] == name
                    ]
                ),
            }
            for name in [
                *sorted(components),
                *(
                    [None]
                    if any(resource["component"] is None for resource in resources)
                    else []
                ),
            ]
        ],
    }


class PlanSummary:
    """Collect one invocation's plans independently of policy outcomes.

    Attributes:
        stacks: Stack coverage and collected change inventories.
        exit_code: Final process result, defaulting to failure until execution finishes.
    """

    def __init__(
        self,
        stacks: Mapping[str, Sequence[str]],
        command: str,
        mode: str,
        selection: Mapping[str, Any] | None = None,
        path: Path | None = None,
        validation_report_path: Path | None = None,
        validation_report_format: str = "json",
    ) -> None:
        self.stacks = {
            name: {
                "name": name,
                "status": "not_run",
                "components": sorted(components),
                "exit_code": None,
                "policy_status": "not_evaluated",
            }
            for name, components in stacks.items()
        }
        self.command = command
        self.mode = mode
        self.selection = dict(selection or {})
        self.started_at = datetime.now(UTC).isoformat()
        self.exit_code = 1
        self.run_id = str(uuid4())
        self.path = path
        self.validation_report_path = validation_report_path
        self.validation_report_format = validation_report_format
        self.validation: dict[str, Any] | None = None

    def attach_validation(self, report: dict[str, Any]) -> None:
        """Link the final validation outcome to this plan report.

        Args:
            report: Machine-readable validation report.
        """
        self.validation = {
            "status": report["status"],
            "summary": report["summary"],
        }

    def start(self, name: str, args: Sequence[str] = ()) -> None:
        """Mark a stack as running.

        Args:
            name: Stack identifier.
            args: Plan arguments used to record explicit targeting.
        """
        self.stacks[name]["status"] = "running"
        self.stacks[name]["targets"] = [
            argument.split("=", 1)[1]
            if argument.startswith("-target=")
            else args[index + 1]
            for index, argument in enumerate(args)
            if argument.startswith("-target=")
            or (argument == "-target" and index + 1 < len(args))
        ]
        self.stacks[name]["selected_components"] = [
            component
            for component in self.stacks[name]["components"]
            if not self.stacks[name]["targets"]
            or any(
                target == f"module.{component}"
                or target.startswith((f"module.{component}.", f"module.{component}["))
                for target in self.stacks[name]["targets"]
            )
        ]

    def collect(self, name: str, plan: dict[str, Any]) -> None:
        """Collect a rendered plan before policy evaluation.

        Args:
            name: Stack identifier.
            plan: Parsed OpenTofu plan document.

        Raises:
            ValueError: If plan metadata is invalid.
            TypeError: If the plan structure is invalid.
        """
        self.stacks[name]["plan"] = summarize_plan(
            plan, self.stacks[name]["components"]
        )

    def finish(
        self, name: str, exit_code: int, *, successful: bool | None = None
    ) -> None:
        """Record execution and policy outcomes for a stack.

        Args:
            name: Stack identifier.
            exit_code: Stack execution result.
            successful: Whether the result represents successful policy evaluation.
        """
        if successful is None:
            successful = exit_code == 0
        self.stacks[name].update(
            status="completed" if "plan" in self.stacks[name] else "failed",
            exit_code=int(exit_code),
            policy_status=("passed" if successful else "failed")
            if "plan" in self.stacks[name]
            else "not_evaluated",
        )

    def payload(self) -> dict[str, Any]:
        """Build the current JSON report, marking unfinished work explicitly.

        Returns:
            Versioned aggregate report.
        """
        totals = Counter({key: 0 for key in _COUNT_KEYS})
        for stack in self.stacks.values():
            if "plan" in stack:
                totals.update(stack["plan"]["totals"])
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "command": self.command,
            "mode": self.mode,
            "selection": self.selection,
            "started_at": self.started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "complete": all(
                stack["status"] == "completed" and stack["plan"]["complete"]
                for stack in self.stacks.values()
            ),
            "empty_selection": not self.stacks,
            "exit_code": int(self.exit_code),
            "totals": dict(totals),
            "validation_status": (
                self.validation["status"] if self.validation is not None else "not_run"
            ),
            "validation_summary": (
                self.validation["summary"] if self.validation is not None else None
            ),
            "validation_report": {
                "format": self.validation_report_format,
                "path": (
                    str(self.validation_report_path)
                    if self.validation_report_path is not None
                    else None
                ),
                "destination": (
                    "file" if self.validation_report_path is not None else "stdout"
                ),
            },
            "stacks": [
                {
                    **stack,
                    "status": "failed"
                    if stack["status"] == "running"
                    else stack["status"],
                }
                for _, stack in sorted(self.stacks.items())
            ],
        }


def _render_report(payload: dict[str, Any], path: Path, detail: str) -> None:
    console = Console(stderr=True, markup=False, highlight=False)
    console.print(
        f"Plan summary — {'complete' if payload['complete'] else 'INCOMPLETE'}"
    )
    console.print(f"{len(payload['stacks'])} stacks · exit code {payload['exit_code']}")
    if payload["empty_selection"]:
        console.print("No stacks selected.")
    table = Table(
        "Stack", "Component", "Create", "Update", "Replace", "Destroy", "Status"
    )
    for stack in payload["stacks"]:
        if "plan" not in stack:
            table.add_row(stack["name"], "—", "—", "—", "—", "—", stack["status"])
            continue
        for component in stack["plan"]["components"]:
            table.add_row(
                stack["name"],
                component["name"] or "(root/unmapped)",
                *(
                    str(component["totals"][key])
                    for key in ("create", "update", "replace", "destroy")
                ),
                "policy failed"
                if stack["policy_status"] == "failed"
                else stack["status"],
            )
    table.add_row(
        "TOTAL",
        "",
        *(
            str(payload["totals"][key])
            for key in ("create", "update", "replace", "destroy")
        ),
        "",
    )
    console.print(table)
    console.print(
        " · ".join(
            f"{value} {key}"
            for key, value in payload["totals"].items()
            if key not in {"create", "update", "replace", "destroy"}
        )
    )
    if detail == "detailed":
        _render_details(console, payload["stacks"])
    console.print(
        "Validation: "
        f"{payload['validation_status']}"
        + (
            " — "
            + ", ".join(
                f"{count} {status}"
                for status, count in payload["validation_summary"].items()
            )
            if payload["validation_summary"] is not None
            else ""
        )
    )
    console.print("Artifacts:")
    console.print(f"  Plan summary: {path}", soft_wrap=True)
    console.print(
        "  Validation report: "
        + (
            payload["validation_report"]["path"]
            if payload["validation_report"]["path"] is not None
            else "stdout"
        ),
        soft_wrap=True,
    )


def _render_details(console: Console, stacks: Sequence[dict[str, Any]]) -> None:
    for stack in stacks:
        for resource in stack.get("plan", {}).get("resources", []):
            annotations = [resource["action"]]
            if resource["import"]:
                annotations.append("import")
            if resource["previous_address"]:
                annotations.append(f"moved from {resource['previous_address']}")
            console.print(
                f"  {stack['name']} / {resource['component'] or '(root/unmapped)'}: "
                f"{resource['address']} ({', '.join(annotations)})"
            )
        for category in ("drift", "deferred"):
            for resource in stack.get("plan", {}).get(category, []):
                console.print(
                    f"  {stack['name']} {category}: {resource['address']} ({resource['action']})"
                )
        for output in stack.get("plan", {}).get("outputs", []):
            console.print(
                f"  {stack['name']} output {output['name']}: {', '.join(output['actions'])}"
            )


def active_plan_summary() -> PlanSummary | None:
    """Return the report belonging to the current execution context.

    Returns:
        Active report, or `None` outside a reporting invocation.
    """
    return _ACTIVE_REPORT.get()


@contextmanager
def plan_summary_session(
    enabled: bool,
    path: Path,
    stacks: Mapping[str, Sequence[str]],
    command: str,
    mode: str,
    detail: str = "table",
    selection: Mapping[str, Any] | None = None,
    validation_report_path: Path | None = None,
    validation_report_format: str = "json",
) -> Iterator[PlanSummary]:
    """Publish an invocation report even when execution fails or is interrupted.

    Args:
        enabled: Whether this invocation plans infrastructure.
        path: JSON artifact destination.
        stacks: Selected stacks and their known component names.
        command: Stacksmith command name.
        mode: Planning mode.
        detail: Console format, `table`, `detailed`, or `none`.
        selection: Filters and targets applied to this invocation.
        validation_report_path: Validation artifact path, when written to a file.
        validation_report_format: Validation report serialization format.

    Yields:
        Mutable invocation report whose exit code is set by the caller.

    Raises:
        OSError: If the report cannot be written.
    """
    report = PlanSummary(
        stacks,
        command,
        mode,
        selection,
        path,
        validation_report_path,
        validation_report_format,
    )
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
