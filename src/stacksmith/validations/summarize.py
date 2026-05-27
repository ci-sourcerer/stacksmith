from typing import Any


def _summarize_value(value: Any, max_len: int = 200) -> str:
    match value:
        case str() if len(value) > max_len:
            return f"{value[:max_len]}... ({len(value)} chars)"
        case str():
            return value
        case bool() | int() | float():
            return str(value)
        case dict():
            return f"dict with {len(value)} keys"
        case list():
            return f"list with {len(value)} items"
        case _:
            return repr(value)[:max_len]


def _extract_plan_values(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None

    planned_values = value.get("planned_values")
    if isinstance(planned_values, dict):
        return planned_values

    if "outputs" in value or "root_module" in value:
        return value

    return None


def _redact_sensitive_plan_value(value: Any, sensitivity: Any = None) -> Any:
    if sensitivity is True:
        return "<sensitive>"

    match value:
        case dict():
            if isinstance(sensitivity, dict):
                return {
                    key: _redact_sensitive_plan_value(
                        child_value,
                        sensitivity.get(key),
                    )
                    for key, child_value in value.items()
                }

            return {
                key: _redact_sensitive_plan_value(child_value)
                for key, child_value in value.items()
            }
        case list():
            if isinstance(sensitivity, list):
                return [
                    _redact_sensitive_plan_value(
                        child_value,
                        sensitivity[index] if index < len(sensitivity) else None,
                    )
                    for index, child_value in enumerate(value)
                ]

            return [_redact_sensitive_plan_value(child_value) for child_value in value]
        case _:
            return value


def _summarize_redacted_value(
    value: Any,
    *,
    max_depth: int = 2,
    max_items: int = 4,
) -> str:
    match value:
        case dict():
            if not value:
                return "{}"
            if max_depth <= 0:
                return f"dict with {len(value)} keys"

            items = []
            for key, child_value in list(value.items())[:max_items]:
                items.append(
                    f"{key}={_summarize_redacted_value(child_value, max_depth=max_depth - 1, max_items=max_items)}"
                )
            if len(value) > max_items:
                items.append("...")
            return "{" + ", ".join(items) + "}"
        case list():
            if not value:
                return "[]"
            if max_depth <= 0:
                return f"list with {len(value)} items"

            items = [
                _summarize_redacted_value(
                    child_value,
                    max_depth=max_depth - 1,
                    max_items=max_items,
                )
                for child_value in value[:max_items]
            ]
            if len(value) > max_items:
                items.append("...")
            return "[" + ", ".join(items) + "]"
        case _:
            return _summarize_value(value)


def _summarize_plan_outputs(outputs: Any) -> str:
    if not isinstance(outputs, dict) or not outputs:
        return ""

    parts: list[str] = []
    for name, output in list(outputs.items())[:4]:
        if isinstance(output, dict):
            summary_value = (
                "<sensitive>" if output.get("sensitive") else output.get("value")
            )
            parts.append(f"{name}={_summarize_redacted_value(summary_value)}")
            continue

        parts.append(f"{name}={_summarize_redacted_value(output)}")

    if len(outputs) > 4:
        parts.append("...")

    return ", ".join(parts)


def _summarize_plan_resource(resource: Any) -> str:
    if not isinstance(resource, dict):
        return _summarize_redacted_value(resource)

    address = resource.get("address", "unknown")
    redacted_values = _redact_sensitive_plan_value(
        resource.get("values"),
        resource.get("sensitive_values"),
    )
    return f"{address}={_summarize_redacted_value(redacted_values)}"


def _summarize_plan_module(module: Any) -> list[str]:
    if not isinstance(module, dict):
        return [_summarize_redacted_value(module)]

    parts: list[str] = []
    resources = module.get("resources", [])
    if isinstance(resources, list):
        for resource in resources[:3]:
            parts.append(_summarize_plan_resource(resource))
        if len(resources) > 3:
            parts.append("...")

    child_modules = module.get("child_modules", [])
    if isinstance(child_modules, list):
        for child_module in child_modules[:2]:
            child_address = (
                child_module.get("address", "unknown")
                if isinstance(child_module, dict)
                else "unknown"
            )
            child_parts = _summarize_plan_module(child_module)
            if child_parts:
                parts.append(f"{child_address}: " + "; ".join(child_parts))
        if len(child_modules) > 2:
            parts.append("...")

    return parts


def _summarize_plan_validation_value(value: Any) -> str | None:
    plan_values = _extract_plan_values(value)
    if plan_values is None:
        return None

    parts: list[str] = []

    outputs_summary = _summarize_plan_outputs(plan_values.get("outputs"))
    if outputs_summary:
        parts.append(f"outputs: {outputs_summary}")

    root_module = plan_values.get("root_module")
    if root_module is not None:
        module_parts = _summarize_plan_module(root_module)
        if module_parts:
            parts.append("root_module: " + " | ".join(module_parts))

    if parts:
        return "; ".join(parts)

    return _summarize_redacted_value(plan_values)


def _summarize_plan_resources(plan_data: dict[str, Any]) -> str:
    changes = plan_data.get("resource_changes", [])
    if not changes:
        return "(no resource changes)"

    addresses = []
    action_counts = {}
    for change in changes[:10]:
        address = change.get("address", "unknown")
        if address not in addresses and len(addresses) < 5:
            addresses.append(address)
        action = change.get("change", {}).get("actions", [None])[0]
        if action:
            action_counts[action] = action_counts.get(action, 0) + 1

    summary = ", ".join(addresses)
    if action_counts:
        counts_str = ", ".join(
            f"{count} {action}{'s' if count > 1 else ''}"
            for action, count in sorted(action_counts.items())
        )
        summary = f"{summary} ({counts_str})"
    return summary
