"""Provider-neutral continuous-integration contracts and services."""

from .contracts import (
    CiCommand,
    CiExecutionManifest,
    CiExecutionRow,
    build_ci_execution_argv,
    parse_ci_stacksmith_args,
    validate_ci_policy,
)
from .service import (
    CiValidationCheckResult,
    inspect_environments,
    prepare_ci_execution,
    validate_ci_inputs,
)

__all__ = [
    "CiCommand",
    "CiExecutionManifest",
    "CiExecutionRow",
    "CiValidationCheckResult",
    "build_ci_execution_argv",
    "parse_ci_stacksmith_args",
    "prepare_ci_execution",
    "inspect_environments",
    "validate_ci_inputs",
    "validate_ci_policy",
]
