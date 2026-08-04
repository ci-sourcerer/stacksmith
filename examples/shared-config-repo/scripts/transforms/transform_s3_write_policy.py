"""Build an S3 write policy JSON for one or more writer principals.

This example transform reads the `STACKSMITH_SSE` environment variable to
determine the default server-side-encryption (SSE) algorithm to require in
generated bucket policies (defaults to `AES256`). It also includes a small
helper that can fetch the current AWS account id via `boto3` when enabled by
environment (opt-in via `STACKSMITH_FETCH_AWS_ACCOUNT_ID`) — this is useful in
examples that list account IDs programmatically.
"""

import json
import logging
import os
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


def _bucket_name_from_inputs(inputs: dict[str, Any]) -> str:
    environment = _slugify(str(inputs.get("environment", "dev")))
    base_name = _slugify(str(inputs.get("bucket_name", "")))
    LOGGER.debug(
        "Building bucket name from inputs with environment=%r, base_name=%r",
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

    LOGGER.debug("Derived bucket name %r", candidate)
    return candidate


def _get_aws_account_id() -> str | None:
    """Attempt to fetch the current AWS account id using boto3.

    This is purposely opt-in: boto3 will only be used when the environment
    variable `STACKSMITH_FETCH_AWS_ACCOUNT_ID` is truthy (1/true/yes). If
    boto3 isn't installed or the call fails, this returns `None`.
    """
    if os.getenv("STACKSMITH_FETCH_AWS_ACCOUNT_ID", "").lower() not in (
        "1",
        "true",
        "yes",
    ):
        LOGGER.debug("AWS account lookup disabled by STACKSMITH_FETCH_AWS_ACCOUNT_ID")
        return None

    try:
        import boto3
    except Exception:
        LOGGER.debug("boto3 unavailable for account lookup", exc_info=True)
        return None

    try:
        sts = boto3.client("sts")
        account_id = sts.get_caller_identity().get("Account")
        LOGGER.info("Fetched AWS account id for example policy helper")
        LOGGER.debug("Fetched AWS account id value=%r", account_id)
        return account_id
    except Exception:
        LOGGER.debug("Failed to fetch AWS account id", exc_info=True)
        return None


EXAMPLE_AWS_ACCOUNT = (
    os.getenv("STACKSMITH_EXAMPLE_AWS_ACCOUNT") or _get_aws_account_id()
)


def transform(value: Any, **context: Any) -> str:
    if isinstance(value, str):
        principals = [value.strip()]
    elif isinstance(value, list):
        principals = [
            entry.strip() for entry in value if isinstance(entry, str) and entry.strip()
        ]
    else:
        principals = []

    LOGGER.debug("Resolved %d writer principal(s)", len(principals))

    if not principals:
        raise ValueError(
            "writer_principal_arn must be a non-empty string or list of strings"
        )

    inputs = context.get("inputs") or {}
    bucket_arn = inputs.get("bucket_arn")
    if bucket_arn is None:
        bucket_name = _bucket_name_from_inputs(inputs)
        bucket_arn = f"arn:aws:s3:::{bucket_name}"
        LOGGER.debug("Computed bucket ARN from inputs: %s", bucket_arn)
    else:
        LOGGER.debug("Using provided bucket ARN: %s", bucket_arn)

    principal_value = principals[0] if len(principals) == 1 else principals

    sse = os.getenv("STACKSMITH_SSE", "AES256")
    LOGGER.debug("Using SSE requirement %r", sse)

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowEC2RoleWriteObjects",
                "Effect": "Allow",
                "Principal": {"AWS": principal_value},
                "Action": [
                    "s3:AbortMultipartUpload",
                    "s3:PutObject",
                    "s3:PutObjectTagging",
                ],
                "Resource": f"{bucket_arn}/*",
                "Condition": {
                    "StringEquals": {
                        "s3:x-amz-server-side-encryption": sse,
                    }
                },
            }
        ],
    }

    LOGGER.info(
        "Generated S3 write policy for %d principal(s) and bucket %s",
        len(principals),
        bucket_arn,
    )
    return json.dumps(policy)
