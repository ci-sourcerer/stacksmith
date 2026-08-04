"""Merge default example tags with incoming tag overrides."""

import logging
from typing import Any

LOGGER = logging.getLogger("transforms")


def transform(value: Any, **context: Any) -> dict[str, Any]:
    inputs = context.get("inputs") or {}
    default_tags = {
        "managed-by": "stacksmith",
        "example": "bucket-and-ec2",
        "environment": str(inputs.get("environment", "dev")),
        "id": str(inputs.get("id", "")),
    }

    incoming_tags = value if isinstance(value, dict) else {}
    LOGGER.debug("Default tags: %r", default_tags)
    LOGGER.debug("Incoming tags: %r", incoming_tags)
    merged_tags = {**default_tags, **incoming_tags}
    LOGGER.info("Merged tags with %d total key(s)", len(merged_tags))
    return merged_tags
