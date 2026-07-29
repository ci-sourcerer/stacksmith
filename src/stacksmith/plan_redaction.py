import json
import tempfile
from pathlib import Path
from typing import Any

REDACTION_MARKER = "<sensitive>"

_PLAN_METADATA_FIELDS = (
    "format_version",
    "terraform_version",
    "timestamp",
    "applyable",
    "complete",
    "errored",
)
_RESOURCE_METADATA_FIELDS = (
    "address",
    "previous_address",
    "module_address",
    "mode",
    "type",
    "name",
    "index",
    "provider_name",
    "schema_version",
    "deposed",
    "action_reason",
    "depends_on",
    "tainted",
    "deposed_key",
)
_CHANGE_METADATA_FIELDS = ("actions",)
_CHECK_ADDRESS_FIELDS = (
    "kind",
    "to_display",
    "mode",
    "type",
    "name",
    "module",
    "instance_key",
)


def _sensitivity_shape_matches(value: Any, sensitivity: Any) -> bool:
    return (
        sensitivity is None
        or isinstance(sensitivity, bool)
        or isinstance(value, dict)
        and isinstance(sensitivity, dict)
        or isinstance(value, list)
        and isinstance(sensitivity, list)
    )


def redact_sensitive_plan_value(value: Any, sensitivity: Any = None) -> Any:
    """Replace sensitive OpenTofu plan values with redaction markers.

    Args:
        value: OpenTofu plan value tree.
        sensitivity: Sensitivity tree corresponding to `value`.

    Returns:
        A copy of the value tree with sensitive nodes replaced.
    """
    if sensitivity is True:
        return REDACTION_MARKER
    if not _sensitivity_shape_matches(value, sensitivity):
        return REDACTION_MARKER

    pending = [(value, sensitivity, False)]
    redacted_values: list[Any] = []
    while pending:
        current_value, current_sensitivity, returning = pending.pop()
        if returning:
            if isinstance(current_value, dict):
                redacted_values.append(
                    dict(
                        zip(
                            current_value,
                            [redacted_values.pop() for _ in range(len(current_value))][
                                ::-1
                            ],
                            strict=True,
                        )
                    )
                )
            elif isinstance(current_value, list):
                redacted_values.append(
                    [redacted_values.pop() for _ in range(len(current_value))][::-1]
                )
            else:
                redacted_values.append(current_value)
            continue

        if current_sensitivity is True:
            redacted_values.append(REDACTION_MARKER)
        elif not _sensitivity_shape_matches(
            current_value,
            current_sensitivity,
        ):
            redacted_values.append(REDACTION_MARKER)
        elif isinstance(current_value, dict):
            pending.append((current_value, current_sensitivity, True))
            for key in reversed(current_value):
                pending.append(
                    (
                        current_value[key],
                        (
                            current_sensitivity.get(key)
                            if isinstance(current_sensitivity, dict)
                            else None
                        ),
                        False,
                    )
                )
        elif isinstance(current_value, list):
            pending.append((current_value, current_sensitivity, True))
            for index in reversed(range(len(current_value))):
                pending.append(
                    (
                        current_value[index],
                        (
                            current_sensitivity[index]
                            if isinstance(current_sensitivity, list)
                            and index < len(current_sensitivity)
                            else None
                        ),
                        False,
                    )
                )
        else:
            redacted_values.append(current_value)

    return redacted_values[0] if redacted_values else value


def _copy_fields(value: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: value[name] for name in names if name in value}


def _redact_output(output: Any) -> Any:
    if not isinstance(output, dict):
        raise ValueError("Plan output entries must be objects.")

    redacted = _copy_fields(output, ("type", "sensitive"))
    if "value" in output:
        redacted["value"] = (
            REDACTION_MARKER if output.get("sensitive") is True else output["value"]
        )
    return redacted


def _redact_resource(resource: Any) -> Any:
    if not isinstance(resource, dict):
        raise ValueError("Plan resource entries must be objects.")

    redacted = _copy_fields(resource, _RESOURCE_METADATA_FIELDS)
    if "values" in resource:
        redacted["values"] = redact_sensitive_plan_value(
            resource["values"],
            resource.get("sensitive_values"),
        )
    if "sensitive_values" in resource:
        redacted["sensitive_values"] = resource["sensitive_values"]
    return redacted


def _redact_module(module: Any) -> Any:
    if not isinstance(module, dict):
        raise ValueError("Plan module entries must be objects.")

    redacted = _copy_fields(module, ("address",))
    if isinstance(module.get("resources"), list):
        redacted["resources"] = [
            _redact_resource(resource) for resource in module["resources"]
        ]
    if isinstance(module.get("child_modules"), list):
        redacted["child_modules"] = [
            _redact_module(child_module) for child_module in module["child_modules"]
        ]
    return redacted


def _redact_values(values: Any) -> Any:
    if not isinstance(values, dict):
        raise ValueError("Plan values representations must be objects.")

    redacted: dict[str, Any] = {}
    if isinstance(values.get("outputs"), dict):
        redacted["outputs"] = {
            name: _redact_output(output) for name, output in values["outputs"].items()
        }
    if "root_module" in values:
        redacted["root_module"] = _redact_module(values["root_module"])
    return redacted


def _redact_change(change: Any) -> Any:
    if not isinstance(change, dict):
        raise ValueError("Plan change representations must be objects.")

    redacted = _copy_fields(change, _CHANGE_METADATA_FIELDS)
    if "before" in change:
        redacted["before"] = redact_sensitive_plan_value(
            change["before"],
            change.get("before_sensitive"),
        )
    if "after" in change:
        redacted["after"] = redact_sensitive_plan_value(
            change["after"],
            change.get("after_sensitive"),
        )
    if "after_unknown" in change:
        redacted["after_unknown"] = redact_sensitive_plan_value(
            change["after_unknown"],
            change.get("after_sensitive"),
        )
    if "before_sensitive" in change:
        redacted["before_sensitive"] = change["before_sensitive"]
    if "after_sensitive" in change:
        redacted["after_sensitive"] = change["after_sensitive"]
    return redacted


def _redact_resource_change(resource_change: Any) -> Any:
    if not isinstance(resource_change, dict):
        raise ValueError("Plan resource change entries must be objects.")

    redacted = _copy_fields(resource_change, _RESOURCE_METADATA_FIELDS)
    if "change" in resource_change:
        redacted["change"] = _redact_change(resource_change["change"])
    return redacted


def _redact_resource_changes(resource_changes: Any) -> Any:
    if not isinstance(resource_changes, list):
        raise ValueError("Plan resource change collections must be arrays.")
    return [
        _redact_resource_change(resource_change) for resource_change in resource_changes
    ]


def _redact_output_changes(output_changes: Any) -> Any:
    if not isinstance(output_changes, dict):
        raise ValueError("Plan output changes must be an object.")
    return {
        name: (
            {"change": _redact_change(output_change["change"])}
            if isinstance(output_change, dict) and "change" in output_change
            else {}
        )
        for name, output_change in output_changes.items()
    }


def _redact_check_address(address: Any) -> Any:
    if not isinstance(address, dict):
        raise ValueError("Plan check addresses must be objects.")
    return _copy_fields(address, _CHECK_ADDRESS_FIELDS)


def _redact_check_instance(instance: Any) -> Any:
    if not isinstance(instance, dict):
        raise ValueError("Plan check instances must be objects.")

    redacted = _copy_fields(instance, ("status",))
    if "address" in instance:
        redacted["address"] = _redact_check_address(instance["address"])
    return redacted


def _redact_checks(checks: Any) -> Any:
    if not isinstance(checks, list):
        raise ValueError("Plan checks must be an array.")

    redacted_checks = []
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("Plan check entries must be objects.")

        redacted = _copy_fields(check, ("status",))
        if "address" in check:
            redacted["address"] = _redact_check_address(check["address"])
        if isinstance(check.get("instances"), list):
            redacted["instances"] = [
                _redact_check_instance(instance) for instance in check["instances"]
            ]
        redacted_checks.append(redacted)
    return redacted_checks


def _redact_state(state: Any) -> Any:
    if not isinstance(state, dict):
        raise ValueError("Prior state must be an object.")

    redacted = _copy_fields(state, ("format_version", "terraform_version"))
    if "values" in state:
        redacted["values"] = _redact_values(state["values"])
    if "checks" in state:
        redacted["checks"] = _redact_checks(state["checks"])
    return redacted


def _redact_deferred_changes(deferred_changes: Any) -> Any:
    if not isinstance(deferred_changes, list):
        raise ValueError("Deferred plan changes must be an array.")

    redacted_changes = []
    for deferred_change in deferred_changes:
        if not isinstance(deferred_change, dict):
            raise ValueError("Deferred plan change entries must be objects.")

        redacted = _copy_fields(deferred_change, ("reason",))
        if "resource_change" in deferred_change:
            redacted["resource_change"] = _redact_resource_change(
                deferred_change["resource_change"]
            )
        redacted_changes.append(redacted)
    return redacted_changes


def _validate_plan_format(plan: dict[str, Any]) -> None:
    format_version = plan.get("format_version")
    if not isinstance(format_version, str) or not format_version:
        raise ValueError("Plan JSON must contain a non-empty format_version string.")
    if format_version.split(".", maxsplit=1)[0] != "1":
        raise ValueError(
            f"Unsupported OpenTofu plan JSON format version: {format_version}"
        )


def redact_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Create a conservative, archive-safe copy of an OpenTofu JSON plan.

    The archive profile omits configuration, input variables, check problem
    messages, generated configuration, import details, replacement paths, and
    unrecognized fields because those locations do not consistently carry
    sensitivity metadata.

    Args:
        plan: Parsed OpenTofu plan JSON document.

    Returns:
        Sanitized plan document containing review-safe metadata and values.

    Raises:
        ValueError: If `plan` does not use a supported JSON format.
    """
    if not isinstance(plan, dict):
        raise ValueError("Plan JSON must contain an object at the document root.")
    _validate_plan_format(plan)

    redacted = _copy_fields(plan, _PLAN_METADATA_FIELDS)
    if "prior_state" in plan:
        redacted["prior_state"] = _redact_state(plan["prior_state"])
    if "planned_values" in plan:
        redacted["planned_values"] = _redact_values(plan["planned_values"])
    if "resource_changes" in plan:
        redacted["resource_changes"] = _redact_resource_changes(
            plan["resource_changes"]
        )
    if "resource_drift" in plan:
        redacted["resource_drift"] = _redact_resource_changes(plan["resource_drift"])
    if "output_changes" in plan:
        redacted["output_changes"] = _redact_output_changes(plan["output_changes"])
    if "deferred_changes" in plan:
        redacted["deferred_changes"] = _redact_deferred_changes(
            plan["deferred_changes"]
        )
    if "checks" in plan:
        redacted["checks"] = _redact_checks(plan["checks"])
    redacted["stacksmith_redaction"] = {
        "marker": REDACTION_MARKER,
        "policy": "archive",
        "version": 1,
    }
    return redacted


def _write_json_atomically(value: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output:
            temporary_path = Path(output.name)
            json.dump(value, output, indent=2)
            output.write("\n")
        temporary_path.replace(output_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_redacted_plan(plan: dict[str, Any], output_path: Path) -> None:
    """Write an archive-safe OpenTofu JSON plan atomically.

    Args:
        plan: Parsed OpenTofu plan JSON document.
        output_path: Destination for the sanitized JSON document.

    Returns:
        None.

    Raises:
        ValueError: If `plan` does not use a supported JSON format.
        OSError: If the destination cannot be written.
    """
    _write_json_atomically(redact_plan(plan), output_path)


def redact_plan_file(input_path: Path, output_path: Path) -> None:
    """Redact an OpenTofu JSON plan file into an archive-safe artifact.

    Args:
        input_path: Source plan JSON path.
        output_path: Destination path, which may equal `input_path`.

    Returns:
        None.

    Raises:
        json.JSONDecodeError: If the input does not contain valid JSON.
        ValueError: If the input does not contain a supported plan document.
        OSError: If either path cannot be read or written.
    """
    plan = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("Plan JSON must contain an object at the document root.")
    write_redacted_plan(plan, output_path)
