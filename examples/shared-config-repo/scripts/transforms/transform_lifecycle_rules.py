"""Prepend default lifecycle policies to user-provided lifecycle rules."""

import logging
from typing import Any

LOGGER = logging.getLogger("transforms")

_DEFAULT_RULES: list[dict[str, Any]] = [
    {
        "id": "abort-incomplete-multipart-uploads",
        "status": "Enabled",
        "abort_incomplete_multipart_upload_days": 7,
    },
    {
        "id": "transition-old-objects-to-ia",
        "status": "Enabled",
        "transition": [
            {
                "days": 30,
                "storage_class": "STANDARD_IA",
            }
        ],
    },
]


def transform(value: Any, **context: Any) -> list[Any]:
    user_rules = value if isinstance(value, list) else []
    LOGGER.debug(
        "Applying lifecycle defaults: default_rules=%d user_rules=%d",
        len(_DEFAULT_RULES),
        len(user_rules),
    )
    merged_rules = _DEFAULT_RULES + user_rules
    LOGGER.info("Built lifecycle rules list with %d total entries", len(merged_rules))
    return merged_rules
