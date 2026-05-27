"""Ensure planned aws_instance resources require IMDSv2 http_tokens."""

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


def _has_required_http_tokens(after_obj: dict[str, Any]) -> bool:
    for metadata in _as_object_list(after_obj.get("metadata_options")):
        if metadata.get("http_tokens") == "required":
            LOGGER.debug("Found required http_tokens in metadata_options")
            return True
    LOGGER.debug("No required http_tokens found in metadata_options")
    return False


def validate(value: Any, **context: Any) -> str:
    for change in value.get("resource_changes") or []:
        if change.get("type") != "aws_instance":
            continue

        LOGGER.debug("Evaluating aws_instance change %r", change.get("address"))

        for after_obj in _as_object_list((change.get("change") or {}).get("after")):
            if not _has_required_http_tokens(after_obj):
                LOGGER.info(
                    "Validation failed: aws_instance %r does not require IMDSv2",
                    change.get("address"),
                )
                return "fail"

    LOGGER.info("Validation passed: all aws_instance resources require IMDSv2")
    return "pass"
