import hashlib
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version
from pathlib import Path
from typing import Any

import yaml
from jsonschema import exceptions as jsonschema_exceptions
from loguru import logger as LOGGER

from .ci.service import (
    inspect_environments,
    prepare_ci_execution,
    validate_ci_inputs,
)
from .constants import (
    CACHE_DIR_NAME,
    DEFAULT_LOCKFILE,
    DEFAULT_STACK_FILES,
    STACKSMITH_DIR_NAME,
)
from .discovery import (
    build_dependency_graph,
    discover_stacks,
    topological_sort,
)
from .enums import MergeMode, TerragruntAction, ValidationReportFormat
from .exceptions import StacksmithConfigError, StacksmithError
from .execution import build_execution_preview
from .formatters import compact_json
from .generation import (
    generate_terragrunt_json,
    generate_tf_json,
    operation_module_name,
    write_terragrunt_json,
    write_tf_json,
)
from .inspector import (
    ComponentTypeInfo,
    PlanPolicyInfo,
    inspect_all,
    inspect_plan_validations,
)
from .loading import (
    load_config,
    load_config_with_locations,
    load_stack,
    load_stack_metadata,
    load_stacks,
)
from .models import (
    ExcludedStackPreview,
    ExecutionPreview,
    FileReference,
    InlineReference,
    LockArtifact,
    LockContext,
    MergeConfig,
    StackDefinition,
    StackLockFile,
    ToolConfig,
    VariableReference,
    render_file_reference,
)
from .plan_redaction import redact_plan, redact_plan_file
from .remote import is_remote_url, parse_git_url, resolve_if_remote, resolve_references
from .runner import run_terragrunt, run_terragrunt_all_ordered
from .targeting import (
    build_terragrunt_args,
    resolve_tag_targets,
    validate_action_options,
)
from .utils import (
    cache_key,
    env_truthy,
    get_current_git_repository,
    stacksmith_env_list,
)
from .validations import PlanValidationOutcome, PlanValidationResult
from .variables import InputLayer, resolve_inputs
from .vendor import get_vendor_dir, load_vendor_manifest

__all__ = [
    "generate_stack",
    "inspect_cache_diagnostics",
    "inspect_dependency_graph",
    "inspect_environments",
    "inspect_modules",
    "lock_stack",
    "prepare_ci_execution",
    "redact_plan",
    "redact_plan_file",
    "run_all_stacks",
    "run_stack_action",
    "run_stack_operation",
    "validate_ci_inputs",
    "validate_stack",
]


def _current_stacksmith_version() -> str:
    try:
        return metadata_version("stacksmith")
    except PackageNotFoundError:
        return "0+unknown"


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _find_git_repo_root(path: Path) -> Path | None:
    start = path if path.is_dir() else path.parent
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _git_commit_for_path(path: Path) -> str | None:
    if (repo_root := _find_git_repo_root(path)) is None:
        return None

    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _resolve_reference_pairs(
    references: Sequence[str | Path | FileReference],
    cache_dir: Path,
    auth_config: Any,
    offline: bool = False,
) -> list[tuple[str, Path]]:
    pairs: list[tuple[str, Path]] = []
    for reference in references:
        rendered = render_file_reference(reference)
        resolved = (
            _resolve_reference_path_offline(reference, cache_dir)
            if offline
            else resolve_if_remote(reference, cache_dir, auth_config=auth_config)
        )
        if not resolved.exists():
            if offline and is_remote_url(reference):
                raise StacksmithConfigError(
                    "Offline mode requires cached artifacts. Missing cached reference: "
                    f"{rendered} (expected at {resolved})"
                )
            raise FileNotFoundError(f"Reference not found: {resolved}")
        pairs.append((rendered, resolved.resolve()))
    return pairs


def _resolve_reference_path_offline(
    reference: str | Path | FileReference,
    cache_dir: Path,
) -> Path:
    rendered = render_file_reference(reference)
    if not is_remote_url(reference):
        return Path(rendered).expanduser()

    if rendered.startswith("git+"):
        parsed = parse_git_url(rendered)
        ref_label = parsed.ref or "HEAD"
        return (
            cache_dir
            / "git"
            / f"{cache_key(parsed.repo_url)}-{cache_key(ref_label)}"
            / parsed.path
        )

    filename = Path(rendered).name or "downloaded"
    return cache_dir / "http" / cache_key(rendered) / filename


def _iter_vars_references(
    vars_file: (
        str | Path | VariableReference | Sequence[str | Path | VariableReference] | None
    ),
    input_layers: Sequence[InputLayer] | None,
) -> list[str | Path | FileReference]:
    refs: list[str | Path | FileReference] = []

    if vars_file is None:
        refs.extend(stacksmith_env_list("VARS") or [])
    elif isinstance(vars_file, (str, Path)) or hasattr(vars_file, "source"):
        refs.append(vars_file)
    else:
        refs.extend(vars_file)

    for kind, value in input_layers or []:
        if kind == "vars":
            refs.append(value)

    file_refs: list[str | Path | FileReference] = []
    for ref in refs:
        if isinstance(ref, InlineReference):
            continue
        file_refs.append(ref)
    return file_refs


def _build_lock_artifact(
    kind: str, reference: str, resolved_path: Path
) -> LockArtifact:
    artifact = LockArtifact(
        kind=kind,
        reference=reference,
        resolved_path=str(resolved_path),
        sha256=_file_sha256(resolved_path),
    )
    if is_remote_url(reference) and reference.startswith("git+"):
        artifact.git_commit = _git_commit_for_path(resolved_path)
    return artifact


def _default_lockfile_path(
    lockfile: Path | None,
    runfiles: Sequence[str | Path | FileReference],
    stack_references: Sequence[str | Path | FileReference],
    stack_paths: Sequence[Path],
) -> Path:
    if lockfile is not None:
        return lockfile.expanduser().resolve()

    if runfiles:
        primary_runfile = runfiles[0]
        if not is_remote_url(primary_runfile):
            return (
                Path(render_file_reference(primary_runfile))
                .expanduser()
                .resolve()
                .parent
                / DEFAULT_LOCKFILE
            )

    if stack_references and not is_remote_url(stack_references[0]):
        return (
            Path(render_file_reference(stack_references[0]))
            .expanduser()
            .resolve()
            .parent
            / DEFAULT_LOCKFILE
        )

    if stack_paths:
        return stack_paths[0].parent / DEFAULT_LOCKFILE
    return Path.cwd() / DEFAULT_LOCKFILE


def _enforce_lock_policy_for_inputs(
    stack_file: Path | str | FileReference | Sequence[Path | str | FileReference],
    config: list[str] | None,
    vars_file: str | Sequence[str] | None,
    input_layers: Sequence[InputLayer] | None,
    runfiles: Sequence[str | Path | FileReference] | None,
    build_dir: Path | None,
    no_cache: bool,
    merge_mode: MergeConfig,
    lockfile: Path | None,
    locked: bool,
    offline: bool,
) -> None:
    if not (locked or offline):
        if env_truthy(
            "WARN_ON_UNLOCKED",
            default=True,
            prefix="STACKSMITH_",
        ):
            LOGGER.warning(
                "Running without lockfile enforcement. Pass `locked=True` with a "
                "Stacksmith lockfile to verify resolved inputs, or set "
                "STACKSMITH_WARN_ON_UNLOCKED=0 to suppress this warning."
            )
        return

    if no_cache and offline:
        raise StacksmithConfigError(
            "--offline cannot be combined with --no-cache because offline mode requires existing local cache artifacts."
        )

    summary = lock_stack(
        stack_file,
        config=config,
        vars_file=vars_file,
        input_layers=input_layers,
        runfiles=runfiles,
        build_dir=build_dir,
        no_cache=False if offline else no_cache,
        merge_mode=merge_mode,
        lockfile=lockfile,
        check=True,
        offline=offline,
    )
    if not summary["lockfile_exists"]:
        raise StacksmithConfigError(
            f"Lockfile not found: {summary['lockfile_path']}. Run 'stacksmith lock' first."
        )
    if not summary["in_sync"]:
        raise StacksmithConfigError(
            f"Lockfile mismatch: {summary['lockfile_path']} does not match resolved inputs."
        )


def _default_config_paths() -> list[str]:
    config_env = stacksmith_env_list("CONFIG")
    if config_env:
        return config_env
    return [str(Path.cwd() / "stacksmith-config.yaml")]


def _resolve_config_paths(
    config_args: list[str | FileReference] | None, cache_dir: Path | None = None
) -> list[Path]:
    raw_paths = config_args if config_args else _default_config_paths()
    resolved = resolve_references(
        raw_paths,
        cache_dir,
        missing_cache_error_factory=lambda reference: StacksmithConfigError(
            f"Cannot fetch remote config without a cache directory: {reference}"
        ),
    )
    LOGGER.debug("Resolved config paths: {paths}", paths=resolved)
    return resolved


def _normalize_stack_refs(
    stack_file: Path | str | FileReference | Sequence[Path | str | FileReference],
) -> list[Path | str | FileReference]:
    if isinstance(stack_file, (Path, str)) or hasattr(stack_file, "source"):
        return [stack_file]

    stack_refs = list(stack_file)
    if not stack_refs:
        raise StacksmithConfigError("At least one stack file path must be provided")
    return stack_refs


def _resolve_stack_paths(
    stack_file: Path | str | FileReference | Sequence[Path | str | FileReference],
    cache_dir: Path | None = None,
) -> list[Path]:
    stack_refs = _normalize_stack_refs(stack_file)
    resolved = resolve_references(
        stack_refs,
        cache_dir,
        missing_cache_error_factory=lambda reference: StacksmithConfigError(
            f"Cannot fetch remote stack without a cache directory: {reference}"
        ),
    )

    if len(resolved) == 1 and not (is_remote_url(stack_refs[0])):
        resolved[0] = _find_stack_file(resolved[0])

    LOGGER.debug("Resolved stack paths: {paths}", paths=resolved)
    return resolved


def _load_stack_definition(
    stack_file: Path | str | FileReference | Sequence[Path | str | FileReference],
    cache_dir: Path | None = None,
    merge_mode: MergeConfig = MergeMode.DEEP,
    template_context: dict[str, Any] | None = None,
) -> StackDefinition:
    stack_paths = _resolve_stack_paths(stack_file, cache_dir)
    if template_context is None:
        stack = (
            load_stack_metadata(stack_paths[0], merge_mode=merge_mode)
            if len(stack_paths) == 1
            else load_stack_metadata(stack_paths, merge_mode=merge_mode)
        )
    else:
        stack = (
            load_stack(
                stack_paths[0],
                merge_mode=merge_mode,
                template_context=template_context,
            )
            if len(stack_paths) == 1
            else load_stacks(
                stack_paths,
                merge_mode=merge_mode,
                template_context=template_context,
            )
        )
    if stack.source_path is None:
        stack.source_path = stack_paths[-1].resolve()
    return stack


def _prepare_stack_definition(
    stack_file: Path | str | FileReference | Sequence[Path | str | FileReference],
    config: ToolConfig,
    vars_path: str | Sequence[str] | None,
    input_layers: Sequence[InputLayer] | None,
    cache_dir: Path | None = None,
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> tuple[StackDefinition, dict[str, Any]]:
    """Resolve stack inputs and render a stack template before validation."""
    metadata = _load_stack_definition(stack_file, cache_dir, merge_mode=merge_mode)
    resolved_inputs = _resolve_stack_inputs(
        metadata,
        config,
        vars_path,
        input_layers,
        cache_dir,
        merge_mode,
    )
    template_context: dict[str, Any] = {
        "inputs": resolved_inputs,
        "stack": {"name": metadata.name, "tags": sorted(metadata.tags)},
    }
    if metadata.source_path is not None and (
        repository := get_current_git_repository(metadata.source_path.parent)
    ):
        template_context["env"] = {"git_repository": repository}
    stack = _load_stack_definition(
        stack_file,
        cache_dir,
        merge_mode=merge_mode,
        template_context=template_context,
    )
    return stack, resolved_inputs


def _resolve_build_dir(stack_path: Path, build_dir: Path | None) -> Path:
    if build_dir:
        return build_dir
    return stack_path.parent / STACKSMITH_DIR_NAME


def _find_stack_file(stack_file: Path) -> Path:
    if stack_file.exists():
        LOGGER.debug(
            "Using explicit stack file path: {stack_file}", stack_file=stack_file
        )
        return stack_file

    if stack_file.name not in DEFAULT_STACK_FILES:
        raise FileNotFoundError(f"Stack file not found: {stack_file}")

    parent = stack_file.parent or Path.cwd()
    for candidate_name in DEFAULT_STACK_FILES:
        candidate = parent / candidate_name
        if candidate.exists():
            LOGGER.debug(
                "Resolved stack file from fallback: {candidate}", candidate=candidate
            )
            return candidate

    raise FileNotFoundError(f"Stack file not found: {stack_file}")


def _resolve_cache_dir(build_dir: Path | None, base: Path | None = None) -> Path:
    if build_dir:
        return build_dir / CACHE_DIR_NAME
    return (base or Path.cwd()) / STACKSMITH_DIR_NAME / CACHE_DIR_NAME


def _clean_cache(cache_dir: Path) -> None:
    if cache_dir.exists():
        LOGGER.debug("Cleaning remote cache: {cache_dir}", cache_dir=cache_dir)
        shutil.rmtree(cache_dir)


def _emit_validation_report(
    report: dict[str, Any],
    report_format: str | ValidationReportFormat = ValidationReportFormat.JSON,
) -> None:
    ValidationReportFormat(report_format)
    print(compact_json(report, sort_keys=True))


def _summarize_plan_validation_results(
    results: Sequence[PlanValidationResult],
) -> dict[str, int]:
    summary = {outcome.value: 0 for outcome in PlanValidationOutcome}
    for result in results:
        summary[result.status.value] += 1
    return summary


def _build_plan_validation_report(
    command: str,
    exit_code: int,
    strict_validation_warnings: bool,
    results: Sequence[PlanValidationResult],
    stack_name: str | None = None,
    stack_count: int | None = None,
) -> dict[str, Any]:
    summary = _summarize_plan_validation_results(results)

    if summary[PlanValidationOutcome.FAIL.value] > 0 or exit_code != 0:
        status = PlanValidationOutcome.FAIL.value
    elif summary[PlanValidationOutcome.WARN.value] > 0:
        status = PlanValidationOutcome.WARN.value
    else:
        status = PlanValidationOutcome.PASS.value

    payload: dict[str, Any] = {
        "command": command,
        "status": status,
        "exit_code": exit_code,
        "strict_validation_warnings": strict_validation_warnings,
        "summary": summary,
        "results": [result.to_dict() for result in results],
    }
    if stack_name is not None:
        payload["stack_name"] = stack_name
    if stack_count is not None:
        payload["stack_count"] = stack_count
    return payload


def _build_validate_report(exit_code: int, message: str) -> dict[str, Any]:
    status = (
        PlanValidationOutcome.PASS.value
        if exit_code == 0
        else PlanValidationOutcome.FAIL.value
    )
    return {
        "command": "validate",
        "status": status,
        "exit_code": exit_code,
        "strict_validation_warnings": False,
        "summary": {
            PlanValidationOutcome.PASS.value: (
                1 if status == PlanValidationOutcome.PASS.value else 0
            ),
            PlanValidationOutcome.WARN.value: 0,
            PlanValidationOutcome.FAIL.value: (
                1 if status == PlanValidationOutcome.FAIL.value else 0
            ),
        },
        "results": [
            {
                "name": "validate",
                "status": status,
                "message": message,
            }
        ],
    }


def load_runtime_config(
    config: list[str] | None,
    build_dir: Path | None,
    base_dir: Path | None = None,
    no_cache: bool = False,
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> tuple[Path, list[Path], ToolConfig]:
    """Resolve and load configuration for a runtime command.

    Args:
        config: Optional configuration file references.
        build_dir: Optional build output directory.
        base_dir: Optional base directory for cache resolution.
        no_cache: Whether to clear the resolved cache before loading.
        merge_mode: Merge strategy for layered configuration files.

    Returns:
        Cache directory, resolved configuration paths, and loaded configuration.
    """
    cache_dir = _resolve_cache_dir(build_dir, base_dir)
    if no_cache:
        _clean_cache(cache_dir)
    config_paths = _resolve_config_paths(config, cache_dir=cache_dir)
    return cache_dir, config_paths, load_config(config_paths, merge_mode=merge_mode)


def _resolve_stacks_for_generation(
    root: Path,
    stack_refs: Sequence[Path | str] | None,
    cache_dir: Path | None = None,
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> dict[str, StackDefinition]:
    if stack_refs:
        stacks = {}
        duplicates = []
        for stack_path in _resolve_stack_paths(stack_refs, cache_dir):
            stack = load_stack_metadata(stack_path, merge_mode=merge_mode)
            if stack.name in stacks:
                duplicates.append(
                    f"  '{stack.name}' defined in both {stacks[stack.name].source_path} and {stack_path}"
                )
                continue
            stacks[stack.name] = stack

        if duplicates:
            raise StacksmithConfigError(
                f"Duplicate stack names found:\n {'\n'.join(duplicates)}"
            )
        return stacks

    return discover_stacks(root)


def _resolve_stack_inputs(
    stack: StackDefinition,
    config: ToolConfig,
    vars_path: str | Sequence[str] | None,
    input_layers: Sequence[InputLayer] | None,
    cache_dir: Path | None = None,
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "stack": {
            "name": stack.name,
            "tags": sorted(stack.tags),
        }
    }
    if stack.source_path is not None and (
        repository := get_current_git_repository(stack.source_path.parent)
    ):
        context["env"] = {"git_repository": repository}
    return resolve_inputs(
        vars_file=vars_path,
        input_layers=input_layers,
        config_validations=config.var_validations or None,
        config_validation_base_path=(
            config.source_path.parent if config.source_path is not None else None
        ),
        cache_dir=cache_dir,
        auth_config=config.remote_auth or None,
        merge_mode=merge_mode,
        context=context,
    )


def _generate_single_stack(
    stack: StackDefinition,
    config: ToolConfig,
    resolved_inputs: dict[str, Any],
    build_dir: Path | None,
    silent: bool = False,
    cache_dir: Path | None = None,
    use_local_modules: bool = False,
    merge_mode: MergeConfig = MergeMode.DEEP,
    operation_names: set[str] | None = None,
) -> Path:
    LOGGER.debug(
        "Resolved variable keys: {keys}",
        keys=sorted(resolved_inputs.keys()),
    )
    if stack.source_path is None:
        raise RuntimeError("Loaded stack is missing a source path")
    output_dir = _resolve_build_dir(stack.source_path, build_dir)

    write_tf_json(
        stack,
        config,
        resolved_inputs,
        output_dir,
        cache_dir=cache_dir,
        auth_config=config.remote_auth or None,
        use_local_modules=use_local_modules,
        operation_names=operation_names,
    )
    write_terragrunt_json(stack, config, resolved_inputs, output_dir)

    if not silent:
        LOGGER.info("Generated files in {output_dir}", output_dir=output_dir)
    return output_dir


@dataclass(frozen=True)
class _PreparedStacks:
    root_build_dir: Path
    stack_build_dirs: dict[str, Path]
    stacks: dict[str, StackDefinition]
    resolved_inputs: dict[str, dict[str, Any]]
    excluded_stacks: list[ExcludedStackPreview]
    state_root: Path | None
    explicit_stack_refs: bool
    custom_build_dir: bool


def _stack_filter_reason(
    stack: StackDefinition,
    include_tags: list[str] | None,
    exclude_tags: list[str] | None,
) -> str | None:
    include = set(include_tags or [])
    exclude = set(exclude_tags or [])
    if include and not include.intersection(stack.tags):
        return (
            f"Did not match any requested include tag ({', '.join(sorted(include))})."
        )
    if matches := sorted(exclude.intersection(stack.tags)):
        return f"Matched excluded stack tags ({', '.join(matches)})."
    return None


def _load_all_stack_definitions(
    discovered_stacks: dict[str, StackDefinition],
    config: ToolConfig,
    vars_path: str | Sequence[str] | None,
    input_layers: Sequence[InputLayer] | None,
    cache_dir: Path | None,
    merge_mode: MergeConfig,
) -> tuple[dict[str, StackDefinition], dict[str, dict[str, Any]]]:
    stacks = {}
    resolved_inputs_by_stack = {}
    for metadata in discovered_stacks.values():
        if metadata.source_path is None:
            raise RuntimeError(f"Stack '{metadata.name}' is missing a source path")
        stack, resolved_inputs = _prepare_stack_definition(
            metadata.source_path,
            config,
            vars_path,
            input_layers,
            cache_dir,
            merge_mode,
        )
        if stack.name in stacks:
            raise StacksmithConfigError(
                f"Duplicate stack name after template rendering: '{stack.name}'"
            )
        stacks[stack.name] = stack
        resolved_inputs_by_stack[stack.name] = resolved_inputs
    return stacks, resolved_inputs_by_stack


def _filter_stacks_with_reasons(
    stacks: dict[str, StackDefinition],
    include_tags: list[str] | None,
    exclude_tags: list[str] | None,
) -> tuple[dict[str, StackDefinition], list[ExcludedStackPreview]]:
    filtered_stacks = {}
    excluded_stacks = []
    for name, stack in stacks.items():
        if reason := _stack_filter_reason(stack, include_tags, exclude_tags):
            if stack.source_path is None:
                raise RuntimeError(f"Stack '{name}' is missing a source path")
            excluded_stacks.append(
                ExcludedStackPreview(
                    name=name,
                    source_path=stack.source_path,
                    reason=reason,
                )
            )
            continue
        filtered_stacks[name] = stack
    return filtered_stacks, excluded_stacks


def _resolve_stack_build_dirs(
    root: Path,
    root_build_dir: Path,
    stacks: dict[str, StackDefinition],
    build_dir: Path | None,
    explicit_stack_refs: bool,
) -> dict[str, Path]:
    stack_build_dirs = {}
    for name, stack in stacks.items():
        if stack.source_path is None:
            raise RuntimeError(f"Stack '{name}' is missing a source path")
        if explicit_stack_refs:
            stack_build_dirs[name] = (
                build_dir / name
                if build_dir is not None
                else _resolve_build_dir(stack.source_path, None)
            )
            continue
        stack_build_dirs[name] = root_build_dir / stack.source_path.parent.relative_to(
            root.resolve()
        )
    return stack_build_dirs


def _prepare_all_stacks(
    root: Path,
    config: ToolConfig,
    vars_path: str | Sequence[str] | None,
    input_layers: Sequence[InputLayer] | None,
    build_dir: Path | None,
    *,
    cache_dir: Path | None = None,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    stack_refs: Sequence[Path | str] | None = None,
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> _PreparedStacks:
    stacks, resolved_inputs_by_stack = _load_all_stack_definitions(
        _resolve_stacks_for_generation(
            root,
            stack_refs,
            cache_dir=cache_dir,
            merge_mode=merge_mode,
        ),
        config,
        vars_path,
        input_layers,
        cache_dir,
        merge_mode,
    )
    LOGGER.debug("Discovered stack names: {stack_names}", stack_names=sorted(stacks))

    filtered_stacks, excluded_stacks = _filter_stacks_with_reasons(
        stacks,
        include_tags,
        exclude_tags,
    )
    LOGGER.debug(
        "Filtered stack names: {stack_names}", stack_names=sorted(filtered_stacks)
    )

    order = topological_sort(build_dependency_graph(filtered_stacks))
    LOGGER.debug("Stack generation order: {order}", order=order)
    ordered_stacks = {name: filtered_stacks[name] for name in order}
    root_build_dir = build_dir or (root / STACKSMITH_DIR_NAME)
    explicit_stack_refs = bool(stack_refs)

    return _PreparedStacks(
        root_build_dir=root_build_dir,
        stack_build_dirs=_resolve_stack_build_dirs(
            root,
            root_build_dir,
            ordered_stacks,
            build_dir,
            explicit_stack_refs,
        ),
        stacks=ordered_stacks,
        resolved_inputs={
            name: resolved_inputs_by_stack[name] for name in ordered_stacks
        },
        excluded_stacks=excluded_stacks,
        state_root=None if explicit_stack_refs else root,
        explicit_stack_refs=explicit_stack_refs,
        custom_build_dir=build_dir is not None,
    )


def _dependency_generation_context(
    name: str,
    prepared: _PreparedStacks,
) -> tuple[dict[str, StackDefinition], dict[str, Path]]:
    stack = prepared.stacks[name]
    return (
        {dependency: prepared.stacks[dependency] for dependency in stack.depends_on},
        {
            dependency: prepared.stack_build_dirs[dependency]
            for dependency in stack.depends_on
        },
    )


def _validate_prepared_stacks(
    prepared: _PreparedStacks,
    config: ToolConfig,
    *,
    cache_dir: Path | None,
    use_local_modules: bool,
) -> None:
    for name, stack in prepared.stacks.items():
        dependency_stacks, dependency_build_dirs = _dependency_generation_context(
            name,
            prepared,
        )
        generate_tf_json(
            stack,
            config,
            prepared.resolved_inputs[name],
            cache_dir=cache_dir,
            auth_config=config.remote_auth or None,
            use_local_modules=use_local_modules,
            root=prepared.state_root,
        )
        generate_terragrunt_json(
            stack,
            config,
            prepared.resolved_inputs[name],
            dependency_stacks,
            dependency_build_dirs,
            root=prepared.state_root,
        )


def _clean_prepared_stacks(prepared: _PreparedStacks) -> None:
    if prepared.custom_build_dir and prepared.root_build_dir.exists():
        LOGGER.debug(
            "Cleaning existing build directory: {root_build_dir}",
            root_build_dir=prepared.root_build_dir,
        )
        shutil.rmtree(prepared.root_build_dir)
        return
    if prepared.explicit_stack_refs:
        for stack_build_dir in prepared.stack_build_dirs.values():
            if stack_build_dir.exists():
                LOGGER.debug(
                    "Cleaning existing build directory: {stack_build_dir}",
                    stack_build_dir=stack_build_dir,
                )
                shutil.rmtree(stack_build_dir)
        return
    if prepared.root_build_dir.exists():
        LOGGER.debug(
            "Cleaning existing build directory: {root_build_dir}",
            root_build_dir=prepared.root_build_dir,
        )
        shutil.rmtree(prepared.root_build_dir)


def _write_prepared_stacks(
    prepared: _PreparedStacks,
    config: ToolConfig,
    *,
    cache_dir: Path | None,
    use_local_modules: bool,
) -> None:
    for name, stack in prepared.stacks.items():
        dependency_stacks, dependency_build_dirs = _dependency_generation_context(
            name,
            prepared,
        )
        write_tf_json(
            stack,
            config,
            prepared.resolved_inputs[name],
            prepared.stack_build_dirs[name],
            cache_dir=cache_dir,
            auth_config=config.remote_auth or None,
            use_local_modules=use_local_modules,
            root=prepared.state_root,
        )
        write_terragrunt_json(
            stack,
            config,
            prepared.resolved_inputs[name],
            prepared.stack_build_dirs[name],
            dependency_stacks,
            dependency_build_dirs,
            root=prepared.state_root,
        )


def _generate_all_stacks(
    root: Path,
    config: ToolConfig,
    vars_path: str | Sequence[str] | None,
    input_layers: Sequence[InputLayer] | None,
    build_dir: Path | None,
    clean: bool = False,
    cache_dir: Path | None = None,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    use_local_modules: bool = False,
    stack_refs: Sequence[Path | str] | None = None,
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> tuple[Path, dict[str, Path], dict[str, StackDefinition]]:
    prepared = _prepare_all_stacks(
        root,
        config,
        vars_path,
        input_layers,
        build_dir,
        cache_dir=cache_dir,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
        stack_refs=stack_refs,
        merge_mode=merge_mode,
    )
    if clean:
        _clean_prepared_stacks(prepared)
    _write_prepared_stacks(
        prepared,
        config,
        cache_dir=cache_dir,
        use_local_modules=use_local_modules,
    )

    if prepared.explicit_stack_refs:
        LOGGER.info(
            "Generated {count} explicit stacks",
            count=len(prepared.stacks),
        )
    else:
        LOGGER.info(
            "Generated {count} stacks in {root_build_dir}",
            count=len(prepared.stacks),
            root_build_dir=prepared.root_build_dir,
        )
    LOGGER.debug(
        "Stack build dirs: {stack_build_dirs}",
        stack_build_dirs=prepared.stack_build_dirs,
    )
    return (
        prepared.root_build_dir,
        prepared.stack_build_dirs,
        prepared.stacks,
    )


def validate_stack(
    stack_file: Path | str | Sequence[Path | str],
    config: list[str] | None = None,
    vars_file: str | Sequence[str] | None = None,
    input_layers: Sequence[InputLayer] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    merge_mode: MergeConfig = MergeMode.DEEP,
    validation_report_format: (
        str | ValidationReportFormat
    ) = ValidationReportFormat.JSON,
) -> dict[str, Any]:
    """Validate a stack definition and its resolved variables.

    Args:
        stack_file: Path, URL, or ordered sequence of stack definition files.
        config: Optional config file paths or URLs. Later entries override earlier ones.
        vars_file: Optional vars file path, URL, or ordered sequence of vars
            file paths or URLs.
        input_layers: Optional ordered CLI input layers merged in call order.
        build_dir: Optional build directory used to derive the cache directory.
        no_cache: When `True`, clear the remote cache before resolving components.
        merge_mode: Merge strategy used for layered stacks, configs, and vars.
        validation_report_format: Format used for machine-readable validation
            report output.

    Returns:
        Validation report payload.
    """
    try:
        cache_dir, config_paths, loaded_config = load_runtime_config(
            config,
            build_dir,
            no_cache=no_cache,
            merge_mode=merge_mode,
        )
        _prepare_stack_definition(
            stack_file,
            loaded_config,
            vars_file,
            input_layers,
            cache_dir,
            merge_mode,
        )
        LOGGER.debug(
            "Validating stack {stack_file} using config paths: {config_paths}",
            stack_file=stack_file,
            config_paths=config_paths,
        )
        report = _build_validate_report(exit_code=0, message="Validation passed")
    except (
        StacksmithError,
        FileNotFoundError,
        OSError,
        ValueError,
        RuntimeError,
        jsonschema_exceptions.ValidationError,
    ) as exc:
        message = str(exc) if str(exc) else f"{type(exc).__name__}"
        LOGGER.error(
            "Validation failed for stack {stack_file} (see validation report for details).",
            stack_file=stack_file,
        )
        report = _build_validate_report(exit_code=1, message=message)

    _emit_validation_report(report, report_format=validation_report_format)
    return report


def inspect_cache_diagnostics(
    stack_file: Path | str | Sequence[Path | str],
    config: list[str] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> dict[str, Any]:
    """Collect stacksmith cache and vendor diagnostics as structured data.

    Args:
        stack_file: Stack file path, URL, or ordered stack layer sequence.
        config: Optional config file paths or URLs.
        build_dir: Optional output directory for generated files.
        no_cache: Whether to clear and refresh remote cache before inspection.
        merge_mode: Merge strategy for layered stack/config inputs.

    Returns:
        Structured diagnostics payload.
    """
    cache_dir, config_paths, _ = load_runtime_config(
        config,
        build_dir,
        no_cache=no_cache,
        merge_mode=merge_mode,
    )
    stack = _load_stack_definition(stack_file, cache_dir, merge_mode=merge_mode)
    if stack.source_path is None:
        raise RuntimeError("Loaded stack is missing a source path")
    build_dir_resolved = _resolve_build_dir(stack.source_path, build_dir)

    remote_cache_entries: list[dict[str, str]] = []
    remote_cache_exists = cache_dir.exists()
    if remote_cache_exists:
        for entry in sorted(cache_dir.iterdir()):
            remote_cache_entries.append(
                {
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                }
            )

    vendor_dir = get_vendor_dir()
    vendor_directory_exists = vendor_dir.exists()
    vendored_modules: list[dict[str, str]] = []
    vendor_manifest_path: str | None = None
    if vendor_directory_exists:
        manifest_path = vendor_dir / "vendor-manifest.json"
        if manifest_path.exists():
            vendor_manifest_path = str(manifest_path)
            manifest = load_vendor_manifest(vendor_dir)
            for key, item in sorted(manifest.items()):
                vendored_modules.append(
                    {
                        "key": key,
                        "source": item.get("source", ""),
                        "version": item.get("version", ""),
                    }
                )

    vendor_directories = (
        [entry.name for entry in sorted(vendor_dir.iterdir()) if entry.is_dir()]
        if vendor_directory_exists
        else []
    )

    return {
        "stack_file": str(stack.source_path),
        "config_paths": [str(path) for path in config_paths],
        "build_directory": str(build_dir_resolved),
        "remote_cache_directory": str(cache_dir),
        "remote_cache_exists": remote_cache_exists,
        "remote_cache_entries": remote_cache_entries,
        "vendor_directory": str(vendor_dir),
        "vendor_directory_exists": vendor_directory_exists,
        "vendor_manifest_path": vendor_manifest_path,
        "vendored_modules": vendored_modules,
        "vendor_directories": vendor_directories,
    }


def generate_stack(
    stack_file: Path | str | Sequence[Path | str],
    config: list[str] | None = None,
    vars_file: str | Sequence[str] | None = None,
    input_layers: Sequence[InputLayer] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    use_local_modules: bool = False,
    merge_mode: MergeConfig = MergeMode.DEEP,
    lockfile: Path | None = None,
    locked: bool = False,
    offline: bool = False,
    runfiles: Sequence[str | Path | FileReference] | None = None,
) -> Path:
    """Generate OpenTofu and Terragrunt files for a single stack.

    Args:
        stack_file: Path, URL, or ordered sequence of stack definition files.
        config: Optional config file paths or URLs. Later entries override earlier ones.
        vars_file: Optional vars file path, URL, or ordered sequence of vars
            file paths or URLs.
        input_layers: Optional ordered CLI input layers merged in call order.
        build_dir: Optional output directory for generated files.
        no_cache: When `True`, clear the remote cache before resolving components.
        use_local_modules: When `True`, rewrite module sources to local vendored paths.
        merge_mode: Merge strategy used for layered stacks, configs, and vars.
        lockfile: Optional lockfile path used by lock policy checks.
        locked: Whether to enforce lockfile verification during generation.
        offline: Whether to require offline-only artifact usage during generation.
        runfiles: Optional runfile references used for lock verification.

    Returns:
        Output directory path containing generated files.
    """
    cache_dir, config_paths, loaded_config = load_runtime_config(
        config,
        build_dir,
        no_cache=no_cache,
        merge_mode=merge_mode,
    )
    _enforce_lock_policy_for_inputs(
        stack_file,
        config,
        vars_file,
        input_layers,
        runfiles,
        build_dir,
        no_cache,
        merge_mode,
        lockfile,
        locked,
        offline,
    )
    LOGGER.debug(
        "Generating stack {stack_file} with config paths: {config_paths}",
        stack_file=stack_file,
        config_paths=config_paths,
    )
    stack, resolved_inputs = _prepare_stack_definition(
        stack_file,
        loaded_config,
        vars_file,
        input_layers,
        cache_dir,
        merge_mode,
    )
    return _generate_single_stack(
        stack,
        loaded_config,
        resolved_inputs,
        build_dir,
        cache_dir=cache_dir,
        use_local_modules=use_local_modules,
        merge_mode=merge_mode,
    )


def lock_stack(
    stack_file: Path | str | FileReference | Sequence[Path | str | FileReference],
    config: list[str | FileReference] | None = None,
    vars_file: (
        str | Path | VariableReference | Sequence[str | Path | VariableReference] | None
    ) = None,
    input_layers: Sequence[InputLayer] | None = None,
    runfiles: Sequence[str | Path | FileReference] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    merge_mode: MergeConfig = MergeMode.DEEP,
    lockfile: Path | None = None,
    check: bool = False,
    offline: bool = False,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Resolve and persist a deterministic lockfile for stack inputs.

    Args:
        stack_file: Stack path, URL, file reference, or ordered stack sequence.
        config: Optional configuration paths or references.
        vars_file: Optional vars source reference(s).
        input_layers: Optional ordered CLI input layers.
        runfiles: Optional runfile reference(s) used for this invocation.
        build_dir: Optional build output directory.
        no_cache: Whether to clear remote cache before resolving references.
        merge_mode: Merge strategy for layered stack and config resolution.
        lockfile: Explicit lockfile output path.
        check: When `True`, verify existing lockfile content without writing.
        offline: When `True`, resolve remote references from cache paths only.
        overwrite: When `False`, create a missing lockfile without replacing an
            existing file. Ignored when `check` is `True`.

    Returns:
        Lock operation summary including output path and sync status.
    """
    cache_dir, config_paths, loaded_config = load_runtime_config(
        config,
        build_dir,
        no_cache=no_cache,
        merge_mode=merge_mode,
    )
    auth_config = loaded_config.remote_auth or None

    stack_references = _normalize_stack_refs(stack_file)
    stack_paths = _resolve_stack_paths(stack_file, cache_dir)
    config_references = config if config is not None else _default_config_paths()
    runfile_references = list(runfiles or [])
    var_references = _iter_vars_references(vars_file, input_layers)

    artifacts: list[LockArtifact] = []
    for reference, resolved_path in zip(stack_references, stack_paths, strict=True):
        artifacts.append(
            _build_lock_artifact(
                "stack", render_file_reference(reference), resolved_path
            )
        )

    for reference, resolved_path in _resolve_reference_pairs(
        config_references,
        cache_dir,
        auth_config,
        offline=offline,
    ):
        artifacts.append(_build_lock_artifact("config", reference, resolved_path))

    for reference, resolved_path in _resolve_reference_pairs(
        runfile_references,
        cache_dir,
        auth_config,
        offline=offline,
    ):
        artifacts.append(_build_lock_artifact("runfile", reference, resolved_path))

    for reference, resolved_path in _resolve_reference_pairs(
        var_references,
        cache_dir,
        auth_config,
        offline=offline,
    ):
        artifacts.append(_build_lock_artifact("vars", reference, resolved_path))

    artifacts = sorted(
        artifacts,
        key=lambda artifact: (
            artifact.kind,
            artifact.reference,
            artifact.resolved_path,
        ),
    )

    lock_document = StackLockFile(
        stacksmith_version=_current_stacksmith_version(),
        context=LockContext(
            stack_paths=[str(path) for path in stack_paths],
            config_paths=[str(path) for path in config_paths],
            runfile_references=[
                render_file_reference(ref) for ref in runfile_references
            ],
        ),
        artifacts=artifacts,
    )
    lock_payload = lock_document.model_dump(mode="json", exclude_none=True)

    lockfile_path = _default_lockfile_path(
        lockfile,
        runfile_references,
        stack_references,
        stack_paths,
    )

    lockfile_exists = lockfile_path.exists()
    in_sync = False
    if lockfile_exists:
        existing_payload = yaml.safe_load(lockfile_path.read_text(encoding="utf-8"))
        in_sync = existing_payload == lock_payload

    if not check and (overwrite or not lockfile_exists):
        lockfile_path.parent.mkdir(parents=True, exist_ok=True)
        lockfile_path.write_text(
            yaml.safe_dump(lock_payload, sort_keys=False),
            encoding="utf-8",
        )
        in_sync = True

    return {
        "lockfile_path": str(lockfile_path),
        "lockfile_exists": lockfile_exists,
        "check": check,
        "in_sync": in_sync,
        "artifact_count": len(artifacts),
    }


def run_stack_operation(
    stack_file: Path | str | Sequence[Path | str],
    operation_name: str,
    config: list[str] | None = None,
    vars_file: str | Sequence[str] | None = None,
    input_layers: Sequence[InputLayer] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    no_cas: bool = False,
    force_rerun: bool = False,
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> dict[str, Any]:
    """Run one approved native operation declared by a stack.

    Args:
        stack_file: Path, URL, or ordered sequence of stack definition files.
        operation_name: Stack-local operation name to execute.
        config: Optional managed config paths or URLs.
        vars_file: Optional vars file paths or URLs.
        input_layers: Optional ordered CLI input layers merged in call order.
        build_dir: Optional directory for generated operation files.
        no_cache: When `True`, clear the Stacksmith remote cache first.
        no_cas: When `True`, disable Terragrunt CAS during this run.
        force_rerun: When `True`, replace the operation runner resource even if
            its execution identity is unchanged.
        merge_mode: Merge strategy for layered configuration and inputs.

    Returns:
        OpenTofu execution metadata.
    """
    cache_dir, _, loaded_config = load_runtime_config(
        config, build_dir, no_cache=no_cache, merge_mode=merge_mode
    )
    stack, resolved_inputs = _prepare_stack_definition(
        stack_file,
        loaded_config,
        vars_file,
        input_layers,
        cache_dir,
        merge_mode,
    )
    if stack.source_path is None:
        raise RuntimeError("Loaded stack is missing a source path")
    output_dir = _generate_single_stack(
        stack,
        loaded_config,
        resolved_inputs,
        build_dir,
        silent=True,
        cache_dir=cache_dir,
        merge_mode=merge_mode,
        operation_names={operation_name},
    )
    return {
        "operation": operation_name,
        "exit_code": run_terragrunt(
            _operation_terragrunt_args(operation_name, force_rerun),
            output_dir,
            auto_approve=True,
            config=loaded_config,
            stack_name=stack.name,
            cache_dir=cache_dir,
            auth_config=loaded_config.remote_auth or None,
            no_cas=no_cas or no_cache,
        ),
    }


def _operation_terragrunt_args(
    operation_name: str,
    force_rerun: bool,
) -> list[str]:
    module_address = f"module.{operation_module_name(operation_name)}"
    args = ["apply", f"-target={module_address}"]
    if force_rerun:
        args.append(f"-replace={module_address}.terraform_data.operation")
    return args


def run_stack_action(
    action: str | TerragruntAction,
    stack_file: Path | str | Sequence[Path | str],
    config: list[str] | None = None,
    vars_file: str | Sequence[str] | None = None,
    input_layers: Sequence[InputLayer] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    auto_approve: bool = False,
    destroy: bool = False,
    use_local_modules: bool = False,
    tags: list[str] | None = None,
    tag_expr: str | None = None,
    save_plan_json: Path | None = None,
    out: Path | None = None,
    plan: Path | None = None,
    strict_validation_warnings: bool = False,
    fail_on_changes: bool = False,
    no_cas: bool = False,
    merge_mode: MergeConfig = MergeMode.DEEP,
    validation_report_format: (
        str | ValidationReportFormat
    ) = ValidationReportFormat.JSON,
    save_redacted_plan_json: Path | None = None,
    lockfile: Path | None = None,
    locked: bool = False,
    offline: bool = False,
    runfiles: Sequence[str | Path | FileReference] | None = None,
) -> int:
    """Generate files for a stack and run a Terragrunt action.

    Args:
        action: Terragrunt action to execute.
        stack_file: Path, URL, or ordered sequence of stack definition files.
        config: Optional config file paths or URLs. Later entries override earlier ones.
        vars_file: Optional vars file path, URL, or ordered sequence of vars
            file paths or URLs.
        input_layers: Optional ordered CLI input layers merged in call order.
        build_dir: Optional output directory for generated files.
        no_cache: When `True`, clear the Stacksmith remote cache before resolving
            components. For Terragrunt execution, this also disables CAS.
        auto_approve: When `True`, pass `--auto-approve` to apply and destroy.
        destroy: When `True` and `action` is `plan`, generate a destroy plan.
        use_local_modules: When `True`, rewrite module sources to local vendored paths.
        tags: Optional list of tags used to select component targets. All listed
            tags must be present on a component for it to match.
        tag_expr: Optional JMESPath expression used to select component targets.
        save_plan_json: Optional file or directory path used to persist rendered
            plan JSON output for plan actions.
        strict_validation_warnings: When `True`, warning outcomes from plan
            validations are treated as failures.
        fail_on_changes: When `True`, return a non-zero exit code if the plan
            contains any component changes.
        no_cas: When `True`, disable Terragrunt CAS during this run.
        merge_mode: Merge strategy used for layered stacks, configs, and vars.
        validation_report_format: Format used for machine-readable validation
            report output.
        save_redacted_plan_json: Optional file or directory path used to persist
            archive-safe redacted plan JSON output for plan actions.
        lockfile: Optional lockfile path used by lock policy checks.
        locked: Whether to enforce lockfile verification before runtime actions.
        offline: Whether to require offline-only artifact usage before runtime actions.
        runfiles: Optional runfile references used for lock verification.

    Returns:
        Process-style exit code from the Terragrunt action.
    """
    cache_dir, config_paths, loaded_config = load_runtime_config(
        config,
        build_dir,
        no_cache=no_cache,
        merge_mode=merge_mode,
    )
    _enforce_lock_policy_for_inputs(
        stack_file,
        config,
        vars_file,
        input_layers,
        runfiles,
        build_dir,
        no_cache,
        merge_mode,
        lockfile,
        locked,
        offline,
    )
    effective_no_cas = no_cas or no_cache
    if no_cache and not no_cas:
        LOGGER.warning(
            "--no-cache now also disables Terragrunt CAS for runtime commands. "
            "Use --no-cas for CAS-only control."
        )
    action_enum = validate_action_options(
        action,
        tags=tags,
        tag_expr=tag_expr,
        save_plan_json=save_plan_json,
        save_redacted_plan_json=save_redacted_plan_json,
        out=out,
        plan=plan,
        tag_support_label="plan, apply, and destroy",
        save_plan_label="plan",
    )

    LOGGER.debug(
        "Running terragrunt action {action} for stack {stack_file} with config paths: {config_paths}",
        action=action_enum.value,
        stack_file=stack_file,
        config_paths=config_paths,
    )
    stack, resolved_inputs = _prepare_stack_definition(
        stack_file,
        loaded_config,
        vars_file,
        input_layers,
        cache_dir,
        merge_mode,
    )
    plan_validation_results: list[PlanValidationResult] = []
    targets = None
    if tags or tag_expr:
        _, _, targets = resolve_tag_targets(
            stack,
            loaded_config,
            tags=tags,
            tag_expr=tag_expr,
        )
        if not targets:
            LOGGER.error(
                "No components in stack '{stack_name}' matched tag selectors",
                stack_name=stack.name,
            )
            if action_enum == TerragruntAction.PLAN:
                _emit_validation_report(
                    _build_plan_validation_report(
                        command=TerragruntAction.PLAN.value,
                        exit_code=1,
                        strict_validation_warnings=strict_validation_warnings,
                        results=plan_validation_results,
                        stack_name=stack.name,
                        stack_count=1,
                    ),
                    report_format=validation_report_format,
                )
            return 1

    output_dir = _generate_single_stack(
        stack,
        loaded_config,
        resolved_inputs,
        build_dir,
        silent=True,
        cache_dir=cache_dir,
        use_local_modules=use_local_modules,
        merge_mode=merge_mode,
    )
    terragrunt_exit_code = run_terragrunt(
        build_terragrunt_args(action_enum, destroy, targets=targets, plan_file=plan),
        output_dir,
        auto_approve=auto_approve,
        config=loaded_config,
        stack_name=stack.name,
        cache_dir=cache_dir,
        auth_config=loaded_config.remote_auth or None,
        save_plan_json=save_plan_json,
        save_redacted_plan_json=save_redacted_plan_json,
        save_plan_binary=out,
        strict_validation_warnings=strict_validation_warnings,
        fail_on_changes=fail_on_changes,
        plan_validation_results=plan_validation_results,
        no_cas=effective_no_cas,
    )

    if action_enum == TerragruntAction.PLAN:
        _emit_validation_report(
            _build_plan_validation_report(
                command=TerragruntAction.PLAN.value,
                exit_code=terragrunt_exit_code,
                strict_validation_warnings=strict_validation_warnings,
                results=plan_validation_results,
                stack_name=stack.name,
                stack_count=1,
            ),
            report_format=validation_report_format,
        )

    return terragrunt_exit_code


def inspect_dependency_graph(
    root: Path,
    action: str | TerragruntAction = TerragruntAction.PLAN,
    config: list[str] | None = None,
    vars_file: str | Sequence[str] | None = None,
    input_layers: Sequence[InputLayer] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    tags: list[str] | None = None,
    tag_expr: str | None = None,
    destroy: bool = False,
    auto_approve: bool = False,
    no_cas: bool = False,
    stacks: Sequence[Path | str] | None = None,
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> ExecutionPreview:
    """Inspect dependency and execution details without writing generated files.

    Args:
        root: Root directory to search for stacks.
        action: Terragrunt action to preview.
        config: Optional config file paths or URLs.
        vars_file: Optional vars file path, URL, or ordered sequence of paths.
        input_layers: Optional ordered CLI input layers merged in call order.
        build_dir: Optional output directory used for path calculation and caching.
        no_cache: Whether to clear the Stacksmith remote cache before resolution.
        include_tags: Optional tags used to include matching stacks.
        exclude_tags: Optional tags used to exclude matching stacks.
        tags: Optional component tags required for selection.
        tag_expr: Optional component-selection expression.
        destroy: Whether a plan should preview destruction.
        auto_approve: Whether apply or destroy would skip approval.
        no_cas: Whether the previewed Terragrunt command should disable CAS.
        stacks: Optional explicit stack paths or URLs.
        merge_mode: Merge strategy used for layered configs, vars, and stacks.

    Returns:
        Structured dependency and execution preview.
    """
    cache_dir, _, loaded_config = load_runtime_config(
        config,
        build_dir,
        base_dir=root,
        no_cache=no_cache,
        merge_mode=merge_mode,
    )
    action_enum = validate_action_options(
        action,
        tags=tags,
        tag_expr=tag_expr,
        save_plan_json=None,
        save_redacted_plan_json=None,
        out=None,
        plan=None,
        tag_support_label="dependency graph plan, apply, and destroy",
        save_plan_label="dependency graph plan",
    )
    prepared = _prepare_all_stacks(
        root,
        loaded_config,
        vars_file,
        input_layers,
        build_dir,
        cache_dir=cache_dir,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
        stack_refs=stacks,
        merge_mode=merge_mode,
    )
    return build_execution_preview(
        action_enum,
        root,
        prepared.stacks,
        prepared.stack_build_dirs,
        loaded_config,
        state_root=prepared.state_root,
        excluded_stacks=prepared.excluded_stacks,
        tags=tags,
        tag_expr=tag_expr,
        destroy=destroy,
        auto_approve=auto_approve,
        no_cas=no_cas or no_cache,
    )


def _validate_dry_run_options(
    dry_run: bool,
    *,
    save_plan_json: Path | None,
    save_redacted_plan_json: Path | None,
    out: Path | None,
    plan: Path | None,
    strict_validation_warnings: bool,
    fail_on_changes: bool,
) -> None:
    if not dry_run:
        return
    unsupported_options = [
        name
        for name, enabled in (
            ("--save-plan-json", save_plan_json is not None),
            ("--save-redacted-plan-json", save_redacted_plan_json is not None),
            ("--out", out is not None),
            ("--plan", plan is not None),
            ("--strict-validation-warnings", strict_validation_warnings),
            ("--fail-on-changes", fail_on_changes),
        )
        if enabled
    ]
    if unsupported_options:
        raise StacksmithConfigError(
            "--dry-run cannot be combined with options that require a Terragrunt "
            f"plan or execution ({', '.join(unsupported_options)})."
        )


def run_all_stacks(
    action: str | TerragruntAction,
    root: Path,
    config: list[str] | None = None,
    vars_file: str | Sequence[str] | None = None,
    input_layers: Sequence[InputLayer] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    clean: bool = False,
    auto_approve: bool = False,
    destroy: bool = False,
    use_local_modules: bool = False,
    tags: list[str] | None = None,
    tag_expr: str | None = None,
    save_plan_json: Path | None = None,
    out: Path | None = None,
    plan: Path | None = None,
    strict_validation_warnings: bool = False,
    fail_on_changes: bool = False,
    no_cas: bool = False,
    stacks: Sequence[Path | str] | None = None,
    merge_mode: MergeConfig = MergeMode.DEEP,
    validation_report_format: (
        str | ValidationReportFormat
    ) = ValidationReportFormat.JSON,
    save_redacted_plan_json: Path | None = None,
    dry_run: bool = False,
) -> int | ExecutionPreview:
    """Generate all discovered stacks and run a Terragrunt action in order.

    Args:
        action: Terragrunt action to execute.
        root: Root directory to search for stacks.
        config: Optional config file paths or URLs. Later entries override earlier ones.
        vars_file: Optional vars file path, URL, or ordered sequence of vars
            file paths or URLs.
        input_layers: Optional ordered CLI input layers merged in call order.
        build_dir: Optional output directory for generated files.
        no_cache: When `True`, clear the Stacksmith remote cache before resolving
            components. For Terragrunt execution, this also disables CAS.
        include_tags: Optional tags used to include matching stacks.
        exclude_tags: Optional tags used to exclude matching stacks.
        clean: When `True`, remove the build directory before generation.
        auto_approve: When `True`, pass `--auto-approve` to apply and destroy.
        destroy: When `True` and `action` is `plan`, generate a destroy plan.
        use_local_modules: When `True`, rewrite module sources to local vendored paths.
        tags: Optional list of tags used to select component targets. All listed
            tags must be present on a component for it to match.
        tag_expr: Optional JMESPath expression used to select component targets.
        save_plan_json: Optional directory used to persist rendered plan JSON
            output for each stack during plan actions.
        strict_validation_warnings: When `True`, warning outcomes from plan
            validations are treated as failures.
        fail_on_changes: When `True`, return a non-zero exit code if the plan
            contains any component changes.
        no_cas: When `True`, disable Terragrunt CAS during this run.
        stacks: Optional explicit stack paths or URLs. When provided, directory
            discovery is skipped and only these stack targets are used.
        merge_mode: Merge strategy used for layered configs and vars, and for
            explicit multi-layer stack refs in single-stack commands.
        validation_report_format: Format used for machine-readable validation
            report output.
        save_redacted_plan_json: Optional directory used to persist archive-safe
            redacted plan JSON output for each stack during plan actions.
        dry_run: When `True`, return an execution preview without writing generated
            files, cleaning build output, or invoking Terragrunt.

    Returns:
        Structured preview for a dry run, otherwise the process-style exit code.
    """
    _validate_dry_run_options(
        dry_run,
        save_plan_json=save_plan_json,
        save_redacted_plan_json=save_redacted_plan_json,
        out=out,
        plan=plan,
        strict_validation_warnings=strict_validation_warnings,
        fail_on_changes=fail_on_changes,
    )
    cache_dir, config_paths, loaded_config = load_runtime_config(
        config,
        build_dir,
        base_dir=root,
        no_cache=no_cache,
        merge_mode=merge_mode,
    )
    effective_no_cas = no_cas or no_cache
    if no_cache and not no_cas:
        LOGGER.warning(
            "--no-cache now also disables Terragrunt CAS for runtime commands. "
            "Use --no-cas for CAS-only control."
        )
    action_enum = validate_action_options(
        action,
        tags=tags,
        tag_expr=tag_expr,
        save_plan_json=save_plan_json,
        save_redacted_plan_json=save_redacted_plan_json,
        out=out,
        plan=plan,
        tag_support_label="run-all plan, apply, and destroy",
        save_plan_label="run-all plan",
    )

    LOGGER.debug(
        "Running run-all action {action} from root {root} with config paths: {config_paths}",
        action=action_enum.value,
        root=root,
        config_paths=config_paths,
    )
    if dry_run:
        prepared = _prepare_all_stacks(
            root,
            loaded_config,
            vars_file,
            input_layers,
            build_dir,
            cache_dir=cache_dir,
            include_tags=include_tags,
            exclude_tags=exclude_tags,
            stack_refs=stacks,
            merge_mode=merge_mode,
        )
        _validate_prepared_stacks(
            prepared,
            loaded_config,
            cache_dir=cache_dir,
            use_local_modules=use_local_modules,
        )
        return build_execution_preview(
            action_enum,
            root,
            prepared.stacks,
            prepared.stack_build_dirs,
            loaded_config,
            state_root=prepared.state_root,
            excluded_stacks=prepared.excluded_stacks,
            tags=tags,
            tag_expr=tag_expr,
            destroy=destroy,
            auto_approve=auto_approve,
            no_cas=effective_no_cas,
            clean=clean,
        )

    _, stack_build_dirs, stacks = _generate_all_stacks(
        root,
        loaded_config,
        vars_file,
        input_layers,
        build_dir,
        clean=clean,
        cache_dir=cache_dir,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
        use_local_modules=use_local_modules,
        stack_refs=stacks,
        merge_mode=merge_mode,
    )

    stack_args_by_name: dict[str, list[str]] | None = None
    plan_validation_results: list[PlanValidationResult] = []
    if tags or tag_expr:
        filtered_stack_dirs = {}
        stack_args_by_name = {}
        for stack_name, stack_dir in stack_build_dirs.items():
            _, _, targets = resolve_tag_targets(
                stacks[stack_name],
                loaded_config,
                tags=tags,
                tag_expr=tag_expr,
            )
            if not targets:
                LOGGER.info(
                    "Skipping stack '{stack_name}': no components matched tag selectors",
                    stack_name=stack_name,
                )
                continue

            filtered_stack_dirs[stack_name] = stack_dir
            stack_args_by_name[stack_name] = build_terragrunt_args(
                action_enum,
                destroy,
                targets=targets,
            )

        if not filtered_stack_dirs:
            LOGGER.info("No stacks matched tag selectors; nothing to run.")
            if action_enum == TerragruntAction.PLAN:
                _emit_validation_report(
                    _build_plan_validation_report(
                        command=f"run-all {TerragruntAction.PLAN.value}",
                        exit_code=0,
                        strict_validation_warnings=strict_validation_warnings,
                        results=plan_validation_results,
                        stack_count=0,
                    ),
                    report_format=validation_report_format,
                )
            return 0

        stack_build_dirs = filtered_stack_dirs

    terragrunt_exit_code = run_terragrunt_all_ordered(
        build_terragrunt_args(action_enum, destroy),
        stack_build_dirs,
        auto_approve=auto_approve,
        config=loaded_config,
        cache_dir=cache_dir,
        auth_config=loaded_config.remote_auth or None,
        stack_args_by_name=stack_args_by_name,
        save_plan_json=save_plan_json,
        save_redacted_plan_json=save_redacted_plan_json,
        strict_validation_warnings=strict_validation_warnings,
        fail_on_changes=fail_on_changes,
        plan_validation_results=plan_validation_results,
        no_cas=effective_no_cas,
    )

    if action_enum == TerragruntAction.PLAN:
        _emit_validation_report(
            _build_plan_validation_report(
                command=f"run-all {TerragruntAction.PLAN.value}",
                exit_code=terragrunt_exit_code,
                strict_validation_warnings=strict_validation_warnings,
                results=plan_validation_results,
                stack_count=len(stack_build_dirs),
            ),
            report_format=validation_report_format,
        )

    return terragrunt_exit_code


def inspect_modules(
    config: list[str] | None = None,
    component_types: list[str] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    merge_mode: MergeConfig = MergeMode.DEEP,
) -> tuple[list[ComponentTypeInfo], list[PlanPolicyInfo]]:
    """Inspect configured modules and return variable/mapping metadata.

    Args:
        config: Optional config file paths or URLs.
        component_types: Specific component types to inspect; inspects all when `None`.
        build_dir: Optional build directory used to derive the cache directory.
        no_cache: When `True`, clear the remote cache before resolving components.
        merge_mode: Merge strategy used for layered configs.

    Returns:
        Tuple of component inspection results and plan policy inspection results.
    """
    cache_dir, config_paths, loaded_config = load_runtime_config(
        config,
        build_dir,
        no_cache=no_cache,
        merge_mode=merge_mode,
    )
    _, config_locations = load_config_with_locations(
        config_paths,
        merge_mode=merge_mode,
    )
    component_results = inspect_all(
        loaded_config,
        component_types=component_types,
        cache_dir=cache_dir,
        auth_config=loaded_config.remote_auth or None,
        config_locations=config_locations,
    )
    plan_policy_results = inspect_plan_validations(loaded_config, config_locations)
    return component_results, plan_policy_results
