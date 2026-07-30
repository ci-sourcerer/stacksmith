import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..enums import DiscoveryMode, ValidationReportFormat
from ..gitops import evaluate_environment_selection
from .contracts import (
    CiExecutionManifest,
    CiExecutionRow,
    parse_ci_stacksmith_args,
    validate_ci_policy,
)


@dataclass(frozen=True)
class CiValidationCheckResult:
    """Result for one `stacksmith ci validate` check."""

    name: str
    status: str
    message: str
    detail: dict[str, Any] | None = None


def _ci_report_status(results: Sequence[CiValidationCheckResult]) -> str:
    return "fail" if any(result.status == "fail" for result in results) else "pass"


def _ci_report_summary(results: Sequence[CiValidationCheckResult]) -> dict[str, int]:
    return {
        "pass": sum(1 for result in results if result.status == "pass"),
        "fail": sum(1 for result in results if result.status == "fail"),
        "total": len(results),
    }


def _file_exists(path: str | None) -> bool:
    return bool(path and Path(path).expanduser().exists())


def inspect_environments(
    gitops_root: str = ".",
    discovery_mode: str = "auto",
    environments: str = "",
    event_name: str = "",
    changed_paths: Sequence[str] | None = None,
    base_ref: str = "",
    before: str = "",
    after: str = "",
) -> dict[str, Any]:
    """Return GitOps environment-selection details for local preview/debugging.

    Args:
        gitops_root: Relative GitOps root path.
        discovery_mode: Environment discovery mode.
        environments: Optional comma-separated manual environment targets.
        event_name: Caller event name for event-aware selection.
        changed_paths: Optional explicit changed paths for selection simulation.
        base_ref: Base branch for pull-request diff mode.
        before: Previous commit SHA for push diff mode.
        after: Current commit SHA for push diff mode.

    Returns:
        Structured environment-selection payload.
    """
    raw_changed_paths = list(changed_paths) if changed_paths is not None else None

    if discovery_mode == "auto":
        last_error: ValueError | None = None
        for candidate_mode in (
            DiscoveryMode.FOLDERS.value,
            DiscoveryMode.ENV_FILES.value,
            DiscoveryMode.FLAT_FILES.value,
        ):
            try:
                selection = evaluate_environment_selection(
                    gitops_root=gitops_root,
                    discovery_mode=candidate_mode,
                    manual_environments=environments,
                    event_name=event_name,
                    changed_paths=raw_changed_paths,
                    base_ref=base_ref,
                    before=before,
                    after=after,
                )
            except ValueError as exc:
                last_error = exc
                continue
            break
        else:
            raise last_error or ValueError("Unable to discover environments.")
    else:
        selection = evaluate_environment_selection(
            gitops_root=gitops_root,
            discovery_mode=discovery_mode,
            manual_environments=environments,
            event_name=event_name,
            changed_paths=raw_changed_paths,
            base_ref=base_ref,
            before=before,
            after=after,
        )
    return {
        "gitops_root": selection.gitops_root,
        "discovery_mode": selection.discovery_mode,
        "common_runfile": selection.common_runfile,
        "all_environments": selection.all_environments,
        "selected_environments": selection.selected_environments,
        "changed_paths": selection.changed_paths,
        "matrix": selection.matrix,
    }


def prepare_ci_execution(
    *,
    command: str,
    operation_name: str = "",
    config_ref: str,
    workdir: str = ".",
    env_file: str = "/dev/null",
    stacksmith_args_json: str = "[]",
    debug: bool = False,
    no_cas: bool = False,
    locked: bool = False,
    offline: bool = False,
    lockfile: str = "",
    force_rerun: bool = False,
    validation_report_format: str = ValidationReportFormat.JSON.value,
    fail_on_changes: bool = False,
    strict_validation_warnings: bool = False,
    gitops_root: str = ".",
    discovery_mode: str = "auto",
    environments: str = "",
    event_name: str = "",
    changed_paths: Sequence[str] | None = None,
    base_ref: str = "",
    before: str = "",
    after: str = "",
    ref_name: str = "",
    default_branch: str = "",
    is_primary_branch: bool | None = None,
    skip_branch_validation: bool = False,
) -> CiExecutionManifest:
    """Prepare one versioned, provider-neutral GitOps CI execution manifest.

    Args:
        command: Stacksmith command to execute.
        operation_name: Stack-local operation name for native operation runs.
        config_ref: Platform-managed Stacksmith config reference.
        workdir: Working directory relative to the checked-out repository.
        env_file: Environment file path, or `/dev/null` to disable implicit loading.
        stacksmith_args_json: JSON array of additional safe Stacksmith arguments.
        debug: Whether to enable debug logging and CI configuration inspection.
        no_cas: Whether to disable content-addressable caching.
        locked: Whether runtime inputs must match the Stacksmith lockfile.
        offline: Whether locked remote inputs must resolve without network access.
        lockfile: Optional explicit Stacksmith lockfile path.
        force_rerun: Whether operations must force execution.
        validation_report_format: Plan validation report format.
        fail_on_changes: Whether plans fail when changes are detected.
        strict_validation_warnings: Whether plan validation warnings fail a plan.
        gitops_root: Relative GitOps root path.
        discovery_mode: Environment discovery mode.
        environments: Optional comma-separated manual environment targets.
        event_name: Normalized provider event name.
        changed_paths: Optional explicit changed paths for selection simulation.
        base_ref: Pull-request target branch name.
        before: Previous push commit SHA.
        after: Current push commit SHA.
        ref_name: Branch name for non-pull-request policy validation.
        default_branch: Repository default branch name.
        is_primary_branch: Provider primary-branch indicator when available.
        skip_branch_validation: Whether branch policy should be skipped.

    Returns:
        A validated manifest that both CI providers can execute.

    Raises:
        StacksmithConfigError: If inputs or policy are invalid.
    """
    validate_ci_policy(
        command=command,
        operation_name=operation_name,
        event_name=event_name,
        ref_name=ref_name,
        base_ref=base_ref,
        default_branch=default_branch,
        is_primary_branch=is_primary_branch,
        skip_branch_validation=skip_branch_validation,
    )
    selection = inspect_environments(
        gitops_root=gitops_root,
        discovery_mode=discovery_mode,
        environments=environments,
        event_name=event_name,
        changed_paths=changed_paths,
        base_ref=base_ref,
        before=before,
        after=after,
    )
    return CiExecutionManifest(
        command=command,
        operation_name=operation_name,
        config_ref=config_ref,
        workdir=workdir,
        env_file=env_file,
        stacksmith_args=parse_ci_stacksmith_args(stacksmith_args_json),
        debug=debug,
        no_cas=no_cas,
        locked=locked,
        offline=offline,
        lockfile=lockfile,
        force_rerun=force_rerun,
        validation_report_format=validation_report_format,
        fail_on_changes=fail_on_changes,
        strict_validation_warnings=strict_validation_warnings,
        matrix=[CiExecutionRow.model_validate(row) for row in selection["matrix"]],
    )


def validate_ci_inputs(
    gitops_root: str = ".",
    discovery_mode: str = "auto",
    runfile: str | None = None,
    env_file: str | None = None,
    validation_report_format: str = ValidationReportFormat.JSON.value,
) -> dict[str, Any]:
    """Validate CI-oriented workflow inputs using an extensible check pipeline.

    Args:
        gitops_root: Relative GitOps root path.
        discovery_mode: Environment discovery mode.
        runfile: Optional explicit runfile path to validate.
        env_file: Optional env file path to validate.
        validation_report_format: Validation report output format.

    Returns:
        Structured check report suitable for future check expansion.
    """
    results: list[CiValidationCheckResult] = []

    try:
        discovery = inspect_environments(
            gitops_root=gitops_root,
            discovery_mode=discovery_mode,
        )
        results.append(
            CiValidationCheckResult(
                name="discovery",
                status="pass",
                message="Discovery mode and GitOps root are valid.",
                detail={
                    "discovery_mode": discovery["discovery_mode"],
                    "gitops_root": discovery["gitops_root"],
                    "common_runfile": discovery["common_runfile"],
                    "environment_count": len(discovery["all_environments"]),
                },
            )
        )
    except (ValueError, subprocess.CalledProcessError) as exc:
        results.append(
            CiValidationCheckResult(
                name="discovery",
                status="fail",
                message=str(exc),
            )
        )
        discovery = None

    if runfile:
        exists = _file_exists(runfile)
        results.append(
            CiValidationCheckResult(
                name="runfile_path",
                status="pass" if exists else "fail",
                message=(
                    "Runfile path exists."
                    if exists
                    else f"Runfile path not found: {Path(runfile).expanduser()}"
                ),
                detail={"path": str(Path(runfile).expanduser())},
            )
        )
    elif discovery is not None:
        common_runfile_path = Path(discovery["common_runfile"]).expanduser()
        if not common_runfile_path.is_absolute():
            common_runfile_path = (
                Path(discovery["gitops_root"] or ".") / common_runfile_path
            )
        exists = common_runfile_path.exists()
        results.append(
            CiValidationCheckResult(
                name="common_runfile",
                status="pass" if exists else "fail",
                message=(
                    "Discovered common runfile exists."
                    if exists
                    else f"Discovered common runfile not found: {common_runfile_path}"
                ),
                detail={"path": str(common_runfile_path)},
            )
        )

    env_file_path = env_file or "/dev/null"
    env_exists = env_file_path == "/dev/null" or _file_exists(env_file_path)
    results.append(
        CiValidationCheckResult(
            name="env_file",
            status="pass" if env_exists else "fail",
            message=(
                "Environment file configuration is valid."
                if env_exists
                else f"Environment file path not found: "
                f"{Path(env_file_path).expanduser()}"
            ),
            detail={"path": env_file_path},
        )
    )

    try:
        results.append(
            CiValidationCheckResult(
                name="validation_report_format",
                status="pass",
                message="Validation report format is supported.",
                detail={
                    "format": ValidationReportFormat(validation_report_format).value
                },
            )
        )
    except ValueError:
        results.append(
            CiValidationCheckResult(
                name="validation_report_format",
                status="fail",
                message=(
                    "Unsupported validation report format "
                    f"'{validation_report_format}'. Supported values: "
                    f"{', '.join(item.value for item in ValidationReportFormat)}."
                ),
            )
        )

    status = _ci_report_status(results)
    return {
        "command": "ci validate",
        "status": status,
        "exit_code": 0 if status == "pass" else 1,
        "summary": _ci_report_summary(results),
        "results": [asdict(result) for result in results],
    }
