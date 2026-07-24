"""Reject plans that expose S3 buckets through public access settings or ACLs."""

import logging
from typing import Any

LOGGER = logging.getLogger("validations")

_PUBLIC_ACLS = {"public-read", "public-read-write", "website"}


def validate(value: Any, **context: Any) -> str:
    if not any(
        change.get("type") in {"aws_s3_bucket_public_access_block", "aws_s3_bucket_acl"}
        for change in value.get("resource_changes") or []
    ):
        LOGGER.info(
            "Validation passed: no S3 public-access resources found; "
            "public-access validation does not apply"
        )
        return "pass"

    for change in value.get("resource_changes") or []:
        after = (change.get("change") or {}).get("after")
        if not after:
            continue

        LOGGER.debug(
            "Inspecting S3 public access-related change type=%r address=%r",
            change.get("type"),
            change.get("address"),
        )

        if change.get("type") == "aws_s3_bucket_public_access_block":
            if (
                after.get("block_public_acls") is not True
                or after.get("block_public_policy") is not True
                or after.get("ignore_public_acls") is not True
                or after.get("restrict_public_buckets") is not True
            ):
                LOGGER.info(
                    "Validation failed: bucket public access block is not fully restrictive"
                )
                return "fail"

        if change.get("type") == "aws_s3_bucket_acl":
            if after.get("acl") in _PUBLIC_ACLS:
                LOGGER.info(
                    "Validation failed: detected public S3 ACL %r", after.get("acl")
                )
                return "fail"

    LOGGER.info("Validation passed: no public S3 exposure detected")
    return "pass"
