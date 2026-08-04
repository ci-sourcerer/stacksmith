"""Normalize bucket names, prefix with environment, and enforce S3 length constraints."""

import logging
import re
from typing import Any

LOGGER = logging.getLogger("transforms")


def _slugify(text: str) -> str:
    lowered = text.lower().replace("_", "-")
    normalized = re.sub(r"[^a-z0-9-]", "-", lowered)
    collapsed = re.sub(r"-{2,}", "-", normalized)
    slug = collapsed.strip("-")
    LOGGER.debug("Slugified value %r -> %r", text, slug)
    return slug


def transform(value: Any, **context: Any) -> str:
    inputs = context.get("inputs") or {}
    environment = _slugify(str(inputs.get("environment", "dev")))
    base_name = _slugify(str(value))
    LOGGER.debug(
        "Transforming bucket name with environment=%r, base_name=%r",
        environment,
        base_name,
    )

    if base_name.startswith(f"{environment}-"):
        candidate = base_name
    elif base_name:
        candidate = f"{environment}-{base_name}"
    else:
        candidate = environment

    candidate = candidate.strip("-")[:63].rstrip("-")
    if len(candidate) < 3:
        candidate = f"{candidate}-bucket"[:63].rstrip("-")

    LOGGER.info("Transformed bucket name to %r", candidate)
    return candidate
