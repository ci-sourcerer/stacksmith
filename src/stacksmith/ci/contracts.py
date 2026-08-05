import json
from collections.abc import Sequence
from typing import Literal, cast

from pydantic import BaseModel, Field, field_validator, model_validator

from ..enums import ValidationReportFormat
from ..exceptions import StacksmithConfigError, StacksmithError

CiCommand = Literal["plan", "apply", "plan-operation", "apply-operation"]


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
        operation_names: Stack-local operation names for native operation plans or runs.
        config_ref: Platform-managed Stacksmith config reference.
        workdir: Working directory relative to the checked-out repository.
        env_file: Optional environment file, with `/dev/null` disabling implicit loading.
        stacksmith_args: Additional validated Stacksmith command arguments.
        debug: Whether to enable debug logging and CI configuration inspection.
        no_cas: Whether to disable content-addressable caching.
        locked: Whether runtime inputs must match the Stacksmith lockfile.
        offline: Whether locked remote inputs must resolve without network access.
        lockfile: Optional explicit Stacksmith lockfile path.
        force_rerun: Whether operations must force execution.
        max_parallel_operations: Maximum independent operations run concurrently.
        validation_report_format: Plan validation report format.
        fail_on_changes: Whether plans fail when changes are detected.
        strict_validation_warnings: Whether plan warnings cause failure.
        matrix: Environment-specific executions.
    """

    version: Literal[2] = 2
    command: CiCommand
    operation_names: list[str] = Field(default_factory=list)
    config_ref: str
    workdir: str = "."
    env_file: str = "/dev/null"
    stacksmith_args: list[str] = Field(default_factory=list)
    debug: bool = False
    no_cas: bool = False
    locked: bool = False
    offline: bool = False
    lockfile: str = ""
    force_rerun: bool = False
    max_parallel_operations: int = Field(default=10, ge=1)
    validation_report_format: str = ValidationReportFormat.JSON.value
    fail_on_changes: bool = False
    strict_validation_warnings: bool = False
    matrix: list[CiExecutionRow] = Field(default_factory=list)

    @field_validator("operation_names")
    @classmethod
    def _normalize_operation_names(cls, operation_names: list[str]) -> list[str]:
        normalized_names = [name.strip() for name in operation_names]
        if any(not name for name in normalized_names):
            raise ValueError("operation names must be non-empty strings")
        if len(set(normalized_names)) != len(normalized_names):
            raise ValueError("operation names must be unique")
        return normalized_names

    @model_validator(mode="after")
    def _validate_manifest(self) -> CiExecutionManifest:
        if not self.config_ref.strip():
            raise ValueError("config_ref must be a non-empty string")
        if (
            self.command not in {"plan-operation", "apply-operation"}
            and self.operation_names
        ):
            raise ValueError(
                "operation names are only supported when command is "
                "'plan-operation' or 'apply-operation'"
            )
        if (
            self.command not in {"plan-operation", "apply-operation"}
            and self.offline
            and not self.locked
        ):
            raise ValueError("offline CI execution requires locked execution")
        ValidationReportFormat(self.validation_report_format)
        return self


def parse_ci_stacksmith_args(value: str) -> list[str]:
    """Parse and validate additional CI Stacksmith arguments.

    Args:
        value: JSON array containing command-line argument strings.

    Returns:
        Validated command-line arguments.

    Raises:
        StacksmithConfigError: If the JSON value is invalid or overrides managed policy.
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
        argument in {"--config", "-c"} or argument.startswith(("--config=", "-c="))
        for argument in arguments
    ):
        raise StacksmithConfigError(
            "stacksmith_args_json cannot override the platform-managed config"
        )
    if any(
        argument == "--runfile" or argument.startswith("--runfile=")
        for argument in arguments
    ):
        raise StacksmithConfigError(
            "stacksmith_args_json cannot override the CI-managed runfiles"
        )
    if any(
        argument in {"--locked", "--offline", "--lockfile"}
        or argument.startswith("--lockfile=")
        for argument in arguments
    ):
        raise StacksmithConfigError(
            "stacksmith_args_json cannot override the platform-managed lock policy"
        )
    return arguments


def validate_ci_policy(
    *,
    command: str,
    operation_names: Sequence[str],
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
        operation_names: Requested stack-local operation names.
        event_name: Normalized provider event name.
        ref_name: Source branch name for non-pull-request events.
        base_ref: Pull-request target branch name.
        default_branch: Repository default branch name when available.
        is_primary_branch: Provider primary-branch indicator when available.
        skip_branch_validation: Whether branch policy should be skipped.

    Raises:
        StacksmithConfigError: If command or branch policy is invalid.
    """
    if command not in {"plan", "apply", "plan-operation", "apply-operation"}:
        raise StacksmithConfigError(
            f"Invalid command '{command}'. Expected 'plan', 'apply', "
            "'plan-operation', or 'apply-operation'."
        )
    if command not in {"plan-operation", "apply-operation"} and operation_names:
        raise StacksmithConfigError(
            "Operation names are only supported when command is "
            "'plan-operation' or 'apply-operation'."
        )
    if skip_branch_validation:
        return
    if event_name == "pull_request":
        if command in {"apply", "apply-operation"}:
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


def resolve_ci_execution_phase(
    manifest: CiExecutionManifest, phase: str = ""
) -> CiCommand:
    """Resolve and validate a lifecycle phase for a CI manifest.

    Args:
        manifest: Provider-neutral CI execution manifest.
        phase: Optional execution phase override.

    Returns:
        Validated phase to execute.

    Raises:
        StacksmithError: If the phase is not valid for the manifest command.
    """
    resolved_phase = phase or (
        "operation" if manifest.command == "apply-operation" else manifest.command
    )
    if (
        resolved_phase
        not in {
            "plan": {"plan", "plan-operation"},
            "apply": {"plan", "plan-operation", "apply", "operation"},
            "plan-operation": {"plan-operation"},
            "apply-operation": {"plan-operation", "operation"},
        }[manifest.command]
    ):
        raise StacksmithError(
            f"CI manifest command '{manifest.command}' cannot execute "
            f"phase '{resolved_phase}'."
        )
    return cast(CiCommand, resolved_phase)


def build_ci_execution_argv(
    manifest: CiExecutionManifest,
    environment: str,
    phase: str = "",
) -> list[str]:
    """Build Stacksmith command arguments for one CI manifest environment.

    Args:
        manifest: Provider-neutral CI execution manifest.
        environment: Environment row to execute.
        phase: Optional lifecycle phase override.

    Returns:
        Stacksmith command arguments for the selected environment.

    Raises:
        StacksmithError: If the manifest does not contain the environment.
    """
    execution_phase = resolve_ci_execution_phase(manifest, phase)
    row = next(
        (
            candidate
            for candidate in manifest.matrix
            if candidate.environment == environment
        ),
        None,
    )
    if row is None:
        raise StacksmithError(
            f"CI execution manifest does not contain environment '{environment}'."
        )
    runfiles = ["--runfile", row.runfile]
    if row.environment_runfile:
        runfiles.extend(["--runfile", row.environment_runfile])
    common_args = [
        "--config",
        manifest.config_ref,
        *manifest.stacksmith_args,
        "--var",
        f"environment={row.environment}",
        "--env-file",
        manifest.env_file,
        *runfiles,
        "--build-dir",
        f".stacksmith-ci/{row.environment}",
    ]
    if manifest.debug:
        common_args.append("--debug")
    if manifest.no_cas:
        common_args.append("--no-cas")
    if execution_phase not in {"plan-operation", "operation"}:
        if manifest.locked:
            common_args.append("--locked")
        if manifest.offline:
            common_args.append("--offline")
        if manifest.lockfile:
            common_args.extend(["--lockfile", manifest.lockfile])
    if execution_phase == "plan":
        return [
            "plan",
            *common_args,
            "--save-redacted-plan-json",
            f".stacksmith-ci/{row.environment}/plan.json",
            "--validation-report-format",
            manifest.validation_report_format,
            *(
                ["--fail-on-changes"]
                if manifest.command == "plan" and manifest.fail_on_changes
                else []
            ),
            *(
                ["--strict-validation-warnings"]
                if manifest.strict_validation_warnings
                else []
            ),
        ]
    if execution_phase == "apply":
        return ["apply", *common_args, "--auto-approve", "--no-after-apply"]
    if execution_phase == "plan-operation":
        return [
            "operation",
            "plan",
            *([",".join(manifest.operation_names)] if manifest.operation_names else []),
            *common_args,
            *(["--after-apply"] if manifest.command in {"plan", "apply"} else []),
            *(["--force-rerun"] if manifest.force_rerun else []),
        ]
    return [
        "operation",
        "run",
        *([",".join(manifest.operation_names)] if manifest.operation_names else []),
        *common_args,
        *(["--force-rerun"] if manifest.force_rerun else []),
    ]
