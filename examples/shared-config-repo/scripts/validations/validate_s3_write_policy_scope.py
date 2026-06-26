"""Ensure PutObject allow statements are principal-scoped and object-path scoped."""

import json
import logging
from typing import Any

LOGGER = logging.getLogger("validations")


def _as_list(value: Any) -> list[Any]:
    if value is None:
        LOGGER.debug("Normalizing None value to empty list")
        return []
    if isinstance(value, list):
        LOGGER.debug("Value already a list with %d item(s)", len(value))
        return value
    LOGGER.debug("Wrapping scalar value into list")
    return [value]


def _allows_put_object(statement: dict[str, Any]) -> bool:
    for action in _as_list(statement.get("Action")):
        if not isinstance(action, str):
            continue
        normalized = action.lower()
        if normalized in {"s3:*", "*", "s3:putobject"}:
            LOGGER.debug("Statement allows PutObject via action %r", action)
            return True
    return False


def _is_wildcard_principal(principal: Any) -> bool:
    if principal == "*":
        LOGGER.debug("Detected wildcard principal '*' ")
        return True
    if not isinstance(principal, dict):
        return False

    aws_principal = principal.get("AWS")
    if aws_principal == "*":
        LOGGER.debug("Detected wildcard principal in AWS field")
        return True
    if isinstance(aws_principal, list) and "*" in aws_principal:
        LOGGER.debug("Detected wildcard principal in AWS principal list")
        return True
    return False


def validate(value: Any, **context: Any) -> str:
    for change in value.get("resource_changes") or []:
        if change.get("type") != "aws_s3_bucket_policy":
            continue

        LOGGER.debug("Inspecting bucket policy change %r", change.get("address"))

        after = (change.get("change") or {}).get("after")
        if not after:
            continue

        raw_policy = after.get("policy")
        if not raw_policy:
            continue

        policy = json.loads(raw_policy) if isinstance(raw_policy, str) else raw_policy
        LOGGER.debug("Loaded bucket policy document")
        statements = _as_list(
            policy.get("Statement") if isinstance(policy, dict) else []
        )
        LOGGER.debug("Policy has %d statement(s)", len(statements))

        for statement in statements:
            if not isinstance(statement, dict):
                continue
            if statement.get("Effect") != "Allow":
                continue
            if not _allows_put_object(statement):
                continue
            if _is_wildcard_principal(statement.get("Principal")):
                LOGGER.info(
                    "Validation failed: PutObject allow statement has wildcard principal"
                )
                return "fail"

            resources = _as_list(statement.get("Resource"))
            if not resources:
                LOGGER.info(
                    "Validation failed: PutObject allow statement missing Resource"
                )
                return "fail"
            for resource in resources:
                if not isinstance(resource, str):
                    LOGGER.info(
                        "Validation failed: PutObject allow resource is not a string"
                    )
                    return "fail"
                if resource == "*":
                    LOGGER.info(
                        "Validation failed: PutObject allow resource cannot be '*'"
                    )
                    return "fail"
                if not resource.endswith("/*"):
                    LOGGER.info(
                        "Validation failed: PutObject allow resource is not object-scoped"
                    )
                    return "fail"

    LOGGER.info("Validation passed: S3 write policy statements are properly scoped")
    return "pass"
