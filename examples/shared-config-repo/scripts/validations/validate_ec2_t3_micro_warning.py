"""Warn when planned EC2 instances use t3.micro in this example."""

import logging
from typing import Any

LOGGER = logging.getLogger("validations")


def _as_object_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        LOGGER.debug("Normalized object payload to single-item list")
        return [raw]
    if isinstance(raw, list):
        normalized = [item for item in raw if isinstance(item, dict)]
        LOGGER.debug("Normalized list payload to %d object(s)", len(normalized))
        return normalized
    LOGGER.debug("Normalized non-object payload to empty list")
    return []


def _collect_t3_micro_addresses(plan: dict[str, Any]) -> list[str]:
    addresses: list[str] = []
    for change in plan.get("resource_changes") or []:
        if change.get("type") != "aws_instance":
            continue

        address = str(change.get("address") or "aws_instance.unknown")
        LOGGER.debug("Inspecting aws_instance change %s", address)
        for after_obj in _as_object_list((change.get("change") or {}).get("after")):
            if after_obj.get("instance_type") == "t3.micro":
                addresses.append(address)
                LOGGER.debug("Found t3.micro instance at %s", address)
    return addresses


def validate(value: Any, **context: Any) -> str | dict[str, str]:
    """Validate the plan and emit a warning for planned t3.micro instances.

    Args:
        value: The Terraform plan document passed by Stacksmith.
        **context: Validation context from Stacksmith, including stack metadata.

    Returns:
        "pass" when no warning is needed, otherwise a warning result object.
    """
    if not isinstance(value, dict):
        LOGGER.debug("Skipping validation because value is not a dict")
        return "pass"

    addresses = sorted(set(_collect_t3_micro_addresses(value)))
    LOGGER.debug("Collected t3.micro addresses: %r", addresses)
    if not addresses:
        LOGGER.info("Validation passed: no t3.micro instances detected")
        return "pass"

    LOGGER.info("Validation warning: detected t3.micro instances in plan")
    return {
        "status": "warn",
        "message": (
            f"Stack '{str(context.get('stack_name') or '<unknown>')}' plans "
            f"t3.micro instance(s): {', '.join(addresses)}. "
            "Consider rightsizing before production."
        ),
    }
