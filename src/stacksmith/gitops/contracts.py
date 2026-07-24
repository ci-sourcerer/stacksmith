import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..enums import ValidationReportFormat
from ..exceptions import StacksmithConfigError

CiCommand = Literal["plan", "apply", "operation"]


class CiExecutionRow(BaseModel):
    """One environment-specific Stacksmith execution from a CI manifest.

    Attributes:
        environment: Deployment environment name.
        runfile: Common Stacksmith runfile.
        environment_runfile: Optional environment-specific runfile overlay.
    """

    environment: str
    runfile: str
    environment_runfile: str = ""


class CiExecutionManifest(BaseModel):
    """Versioned, provider-neutral instructions for GitOps CI execution.

    Attributes:
        version: Manifest schema version.
        command: Stacksmith command to execute.
        operation_name: Stack-local operation name for native operation runs.
        config_ref: Platform-managed Stacksmith config reference.
        workdir: Working directory relative to the checked-out repository.
        env_file: Optional environment file, with `/dev/null` disabling implicit loading.
        stacksmith_args: Additional validated Stacksmith command arguments.
        no_cas: Whether to disable content-addressable caching.
        force_rerun: Whether operations must force execution.
        validation_report_format: Plan validation report format.
        fail_on_changes: Whether plans fail when changes are detected.
        strict_validation_warnings: Whether plan warnings cause failure.
        matrix: Environment-specific executions.
    """

    version: Literal[1] = 1
    command: CiCommand
    operation_name: str = ""
    config_ref: str
    workdir: str = "."
    env_file: str = "/dev/null"
    stacksmith_args: list[str] = Field(default_factory=list)
    no_cas: bool = False
    force_rerun: bool = False
    validation_report_format: str = ValidationReportFormat.JSON.value
    fail_on_changes: bool = False
    strict_validation_warnings: bool = False
    matrix: list[CiExecutionRow] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_manifest(self) -> "CiExecutionManifest":
        if not self.config_ref.strip():
            raise ValueError("config_ref must be a non-empty string")
        if self.command == "operation" and not self.operation_name.strip():
            raise ValueError("operation_name is required when command is 'operation'")
        if self.command != "operation" and self.operation_name:
            raise ValueError(
                "operation_name is only supported when command is 'operation'"
            )
        ValidationReportFormat(self.validation_report_format)
        return self


def parse_ci_stacksmith_args(value: str) -> list[str]:
    """Parse and validate additional CI Stacksmith arguments.

    Args:
        value: JSON array containing command-line argument strings.

    Returns:
        Validated command-line arguments.

    Raises:
        StacksmithConfigError: If the JSON value is invalid or overrides managed config.
    """
    try:
        arguments = json.loads(value or "[]")
    except json.JSONDecodeError as exc:
        raise StacksmithConfigError(
            "stacksmith_args_json must be a JSON array of strings"
        ) from exc
    if not isinstance(arguments, list) or not all(
        isinstance(argument, str) for argument in arguments
    ):
        raise StacksmithConfigError(
            "stacksmith_args_json must be a JSON array of strings"
        )
    if any("\0" in argument for argument in arguments):
        raise StacksmithConfigError(
            "stacksmith_args_json entries cannot contain NUL bytes"
        )
    if any(
        argument in {"--config", "-c"}
        or argument.startswith("--config=")
        or argument.startswith("-c=")
        for argument in arguments
    ):
        raise StacksmithConfigError(
            "stacksmith_args_json cannot override the platform-managed config"
        )
    return arguments


def validate_ci_policy(
    *,
    command: str,
    operation_name: str,
    event_name: str,
    ref_name: str,
    base_ref: str,
    default_branch: str,
    is_primary_branch: bool | None,
    skip_branch_validation: bool,
) -> None:
    """Validate provider-neutral command and branch policy for CI invocation.

    Args:
        command: Requested Stacksmith command.
        operation_name: Requested stack-local operation name.
        event_name: Normalized provider event name.
        ref_name: Source branch name for non-pull-request events.
        base_ref: Pull-request target branch name.
        default_branch: Repository default branch name when available.
        is_primary_branch: Provider primary-branch indicator when available.
        skip_branch_validation: Whether branch policy should be skipped.

    Raises:
        StacksmithConfigError: If command or branch policy is invalid.
    """
    if command not in {"plan", "apply", "operation"}:
        raise StacksmithConfigError(
            f"Invalid command '{command}'. Expected 'plan', 'apply', or 'operation'."
        )
    if command == "operation" and not operation_name.strip():
        raise StacksmithConfigError(
            "operation_name is required when command is 'operation'."
        )
    if command != "operation" and operation_name:
        raise StacksmithConfigError(
            "operation_name is only supported when command is 'operation'."
        )
    if skip_branch_validation:
        return
    if event_name == "pull_request":
        if command in {"apply", "operation"}:
            raise StacksmithConfigError(
                f"'{command}' is not allowed on pull requests. Use 'plan' instead."
            )
        if default_branch and base_ref != default_branch:
            raise StacksmithConfigError(
                f"Pull request operations must target the default branch '{default_branch}'."
            )
        return
    if default_branch and ref_name != default_branch:
        raise StacksmithConfigError(
            "Operations are only allowed on the default branch or pull requests to it. "
            f"Current branch: {ref_name}"
        )
    if not default_branch and is_primary_branch is False:
        raise StacksmithConfigError(
            "Operations are only allowed on the primary branch or pull requests to it. "
            f"Current branch: {ref_name}"
        )
