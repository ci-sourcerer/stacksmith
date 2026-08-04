import logging
import os
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from stacksmith.utils import parse_bool, stacksmith_env_int

from ..constants import CACHE_DIR_NAME, STACKSMITH_DIR_NAME
from ..enums import MergeMode, ValidationReportFormat
from ..exceptions import StacksmithConfigError
from ..gitops import evaluate_environment_selection
from ..input_parsing import parse_operation_names
from ..loading import load_config, load_runfiles
from ..models import MergeConfig, MergePolicy, RunFile
from ..remote import is_remote_url, resolve_references
from .contracts import (
    CiExecutionManifest,
    CiExecutionRow,
    parse_ci_stacksmith_args,
    validate_ci_policy,
)

LOGGER = logging.getLogger(__name__)


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


def _ci_config_reference(config_ref: str, workdir: str) -> str | Path:
    config_path = Path(config_ref).expanduser()
    if is_remote_url(config_ref) or config_path.is_absolute():
        return config_ref
    return Path(workdir).expanduser() / config_path


def _ci_config_ref_key(config_ref: str | Path) -> str:
    if isinstance(config_ref, Path):
        return str(config_ref.resolve())
    return config_ref


def _ci_config_split(config_ref: str) -> list[str]:
    refs: list[str] = []
    buffer = []
    index = 0
    while index < len(config_ref):
        if config_ref.startswith("://", index):
            buffer.append(config_ref[index : index + 3])
            index += 3
            continue
        if config_ref[index] == ":":
            refs.append("".join(buffer))
            buffer = []
            index += 1
            continue
        buffer.append(config_ref[index])
        index += 1
    if buffer:
        refs.append("".join(buffer))
    return [ref.strip() for ref in refs if ref.strip()]


def _ci_config_references(config_ref: str, workdir: str) -> list[str | Path]:
    results = []
    seen = set()
    for candidate in _ci_config_split(config_ref.strip()):
        resolved = _ci_config_reference(candidate, workdir)
        key = _ci_config_ref_key(resolved)
        if key in seen:
            LOGGER.warning(
                "CI config_ref %r is duplicated and will be ignored",
                candidate,
            )
            continue
        seen.add(key)
        results.append(resolved)
    return results


def _effective_ci_backend_type(
    manifest: CiExecutionManifest, row: CiExecutionRow
) -> str:
    runfile = load_runfiles(
        [
            Path(row.runfile).expanduser(),
            *(
                [Path(row.environment_runfile).expanduser()]
                if row.environment_runfile
                else []
            ),
        ]
    )
    workdir_path = (
        Path(manifest.workdir).expanduser() / STACKSMITH_DIR_NAME / CACHE_DIR_NAME
    )

    # Resolve all config references
    ci_config_refs = _ci_config_references(manifest.config_ref, manifest.workdir)
    all_config_refs = [*runfile.configs, *ci_config_refs]
    resolved_refs = resolve_references(all_config_refs, workdir_path)

    return (
        load_config(
            resolved_refs,
            merge_mode=_ci_merge_config(manifest.stacksmith_args, runfile),
        )
        .backend.type.strip()
        .lower()
    )


def _ci_merge_config(arguments: Sequence[str], runfile: RunFile) -> MergeConfig:
    explicit_mode = None
    for index, argument in enumerate(arguments):
        if argument == "--merge-mode":
            if index + 1 >= len(arguments):
                raise StacksmithConfigError(
                    "stacksmith_args_json is missing a value for --merge-mode"
                )
            explicit_mode = MergeMode(arguments[index + 1])
        elif argument.startswith("--merge-mode="):
            explicit_mode = MergeMode(argument.partition("=")[2])
    if explicit_mode is not None:
        return explicit_mode
    if runfile.merge_rules:
        return MergePolicy(
            default=runfile.merge_mode or MergeMode.DEEP,
            rules=runfile.merge_rules,
        )
    return runfile.merge_mode or MergeMode.DEEP


def _validate_ci_backends(manifest: CiExecutionManifest) -> None:
    if parse_bool(os.getenv("STACKSMITH_CI_SKIP_BACKEND_VALIDATION")):
        return

    for row in manifest.matrix:
        if _effective_ci_backend_type(manifest, row) == "local":
            raise StacksmithConfigError(
                f"CI prepare rejected environment '{row.environment}': "
                "the local backend is not supported. "
                "Configure a remote backend for CI."
            )


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
    selection = evaluate_environment_selection(
        gitops_root=gitops_root,
        discovery_mode=discovery_mode,
        manual_environments=environments,
        event_name=event_name,
        changed_paths=list(changed_paths) if changed_paths is not None else None,
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
    operation_names: str = "",
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
        operation_names: Comma-delimited stack-local operation names.
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
    selected_operation_names = (
        parse_operation_names(operation_names) if operation_names.strip() else []
    )
    validate_ci_policy(
        command=command,
        operation_names=selected_operation_names,
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
    manifest = CiExecutionManifest(
        command=command,
        operation_names=selected_operation_names,
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
        max_parallel_operations=stacksmith_env_int(
            "MAX_PARALLEL_OPERATIONS", 10, minimum=1
        ),
        validation_report_format=validation_report_format,
        fail_on_changes=fail_on_changes,
        strict_validation_warnings=strict_validation_warnings,
        matrix=[CiExecutionRow.model_validate(row) for row in selection["matrix"]],
    )
    _validate_ci_backends(manifest)
    return manifest


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
