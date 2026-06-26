"""Configure the shared secondary AWS provider."""

import logging
import os
from functools import lru_cache
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import Any

LOGGER = logging.getLogger("providers")


@lru_cache(maxsize=1)
def _load_identity_helpers() -> ModuleType:
    script_path = Path((lambda: None).__code__.co_filename).resolve()
    helper_path = script_path.with_name("aws_identity.py")
    LOGGER.debug("Loading AWS identity helper module from %s", helper_path)
    spec = spec_from_file_location("stacksmith_examples_aws_identity", helper_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load helper module from: {helper_path}")

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    LOGGER.info("Loaded AWS identity helper module")
    return module


def config(**context: Any) -> dict[str, Any]:
    """Build provider arguments for the shared secondary AWS provider.

    Args:
        **context: Provider evaluation context from stacksmith.

    Returns:
        Provider argument mapping for the secondary AWS provider instance.
    """
    stack_name = context.get("stack_name", "stacksmith")
    inputs = context.get("inputs") or {}
    profile_name = (
        inputs.get("aws_profile")
        or os.getenv("AWS_PROFILE")
        or os.getenv("AWS_DEFAULT_PROFILE")
    )
    LOGGER.debug(
        "Building secondary provider config for stack=%r with profile=%r",
        stack_name,
        profile_name,
    )

    provider_config = {
        "region": "us-west-2",
    }
    if _load_identity_helpers().is_root_aws_identity(profile_name):
        LOGGER.info("Using direct provider config for root AWS identity")
        return provider_config

    provider_config["assume_role"] = {
        "role_arn": f"arn:aws:iam::123456789012:role/{stack_name}-secondary",
        "external_id": f"stacksmith-{stack_name}",
        "session_name": "stacksmith-secondary",
    }
    LOGGER.info("Configured secondary provider to assume role for stack=%r", stack_name)
    LOGGER.debug("Secondary provider config: %r", provider_config)
    return provider_config
