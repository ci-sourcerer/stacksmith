"""AWS identity helper functions."""

import logging
import os

LOGGER = logging.getLogger("providers")


def is_root_aws_identity(profile_name: str | None = None) -> bool:
    """Return whether the active AWS credentials resolve to a root principal.

    Args:
        profile_name: Optional AWS shared credentials profile name.

    Returns:
        `True` when STS caller identity resolves to a root ARN, else `False`.
    """
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError, ProfileNotFound
    except ImportError:
        LOGGER.warning(
            "boto3 unavailable; unable to deterministically resolve AWS identity"
        )
        if profile_name == "root" or os.getenv("IS_ROOT_AWS_IDENTITY", "").lower() in (
            "1",
            "true",
            "yes",
        ):
            return True
        return False

    try:
        LOGGER.debug("Resolving AWS caller identity for profile=%r", profile_name)
        session = (
            boto3.session.Session(profile_name=profile_name)
            if profile_name
            else boto3.session.Session()
        )
        identity = session.client("sts").get_caller_identity()
    except BotoCoreError, ClientError, ProfileNotFound, ValueError:
        LOGGER.debug("Failed to resolve AWS identity", exc_info=True)
        return False

    arn = str(identity.get("Arn", ""))
    is_root = arn.endswith(":root")
    LOGGER.debug("Resolved AWS caller ARN=%r, is_root=%s", arn, is_root)
    if is_root:
        LOGGER.info("Detected root AWS identity")
    return is_root
