import json
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import jmespath
from jmespath import exceptions as jmespath_exceptions
from jsonschema import exceptions as jsonschema_exceptions
from loguru import logger as LOGGER

from .discovery import (
    build_dependency_graph,
    discover_stacks,
    filter_stacks_by_tags,
    topological_sort,
)
from .enums import (
    DiscoveryMode,
    MergeMode,
    TerragruntAction,
    ValidationReportFormat,
)
from .exceptions import StacksmithConfigError, StacksmithError
from .generator import operation_module_name, write_tf_json
from .gitops import evaluate_environment_selection
from .inspector import (
    ComponentTypeInfo,
    PlanPolicyInfo,
    inspect_all,
    inspect_plan_policies,
)
from .loader import (
    load_config,
    load_config_with_locations,
    load_stack,
    load_stack_metadata,
    load_stacks,
)
from .models import (
    FileReference,
    StackDefinition,
    ToolConfig,
)
from .remote import is_remote_url, resolve_references
from .runner import run_terragrunt, run_terragrunt_all_ordered
from .terragrunt import write_terragrunt_json
from .utils import print_to_stderr, stacksmith_env_list
from .validation import PlanValidationResult
from .validations.outcomes import PlanValidationOutcome
from .variables import InputLayer, resolve_inputs
from .vendor import get_vendor_dir, load_vendor_manifest


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
    passed = sum(1 for result in results if result.status == "pass")
    failed = sum(1 for result in results if result.status == "fail")
    return {
        "pass": passed,
        "fail": failed,
        "total": len(results),
    }


def _file_exists(path: str | None) -> bool:
    if not path:
        return False
    return Path(path).expanduser().exists()


def inspect_environments(
    *,
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


def validate_ci_inputs(
    *,
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
        common_runfile = str(common_runfile_path)
        exists = common_runfile_path.exists()
        results.append(
            CiValidationCheckResult(
                name="common_runfile",
                status="pass" if exists else "fail",
                message=(
                    "Discovered common runfile exists."
                    if exists
                    else f"Discovered common runfile not found: {common_runfile}"
                ),
                detail={"path": common_runfile},
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
                else f"Environment file path not found: {Path(env_file_path).expanduser()}"
            ),
            detail={"path": env_file_path},
        )
    )

    try:
        resolved_format = ValidationReportFormat(validation_report_format).value
        results.append(
            CiValidationCheckResult(
                name="validation_report_format",
                status="pass",
                message="Validation report format is supported.",
                detail={"format": resolved_format},
            )
        )
    except ValueError:
        supported = ", ".join(item.value for item in ValidationReportFormat)
        results.append(
            CiValidationCheckResult(
                name="validation_report_format",
                status="fail",
                message=(
                    "Unsupported validation report format "
                    f"'{validation_report_format}'. Supported values: {supported}."
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
    merge_mode: str | MergeMode = MergeMode.DEEP,
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
    merge_mode: str | MergeMode = MergeMode.DEEP,
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
    template_context = {
        "inputs": resolved_inputs,
        "stack": {"name": metadata.name, "tags": sorted(metadata.tags)},
    }
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
    return stack_path.parent / ".stacksmith"


def _find_stack_file(stack_file: Path) -> Path:
    if stack_file.exists():
        LOGGER.debug(
            "Using explicit stack file path: {stack_file}", stack_file=stack_file
        )
        return stack_file

    fallback_names = ["stack.yaml", "stack.yml", "stack.json"]
    if stack_file.name not in fallback_names:
        raise FileNotFoundError(f"Stack file not found: {stack_file}")

    parent = stack_file.parent or Path.cwd()
    for candidate_name in fallback_names:
        candidate = parent / candidate_name
        if candidate.exists():
            LOGGER.debug(
                "Resolved stack file from fallback: {candidate}", candidate=candidate
            )
            return candidate

    raise FileNotFoundError(f"Stack file not found: {stack_file}")


def _resolve_cache_dir(build_dir: Path | None, base: Path | None = None) -> Path:
    if build_dir:
        return build_dir / ".cache"
    return (base or Path.cwd()) / ".stacksmith" / ".cache"


def _clean_cache(cache_dir: Path) -> None:
    if cache_dir.exists():
        LOGGER.debug("Cleaning remote cache: {cache_dir}", cache_dir=cache_dir)
        shutil.rmtree(cache_dir)


def _emit_validation_report(
    report: dict[str, Any],
    *,
    report_format: str | ValidationReportFormat = ValidationReportFormat.JSON,
) -> None:
    ValidationReportFormat(report_format)
    print(json.dumps(report, sort_keys=True))


def _summarize_plan_validation_results(
    results: Sequence[PlanValidationResult],
) -> dict[str, int]:
    summary = {outcome.value: 0 for outcome in PlanValidationOutcome}
    for result in results:
        summary[result.status.value] += 1
    return summary


def _build_plan_validation_report(
    *,
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


def _load_runtime_config(
    config: list[str] | None,
    build_dir: Path | None,
    *,
    base_dir: Path | None = None,
    no_cache: bool = False,
    merge_mode: str | MergeMode = MergeMode.DEEP,
) -> tuple[Path, list[Path], ToolConfig]:
    cache_dir = _resolve_cache_dir(build_dir, base_dir)
    if no_cache:
        _clean_cache(cache_dir)
    config_paths = _resolve_config_paths(config, cache_dir=cache_dir)
    return cache_dir, config_paths, load_config(config_paths, merge_mode=merge_mode)


def _compile_tag_expression(tag_expr: str):
    try:
        return jmespath.compile(tag_expr)
    except jmespath_exceptions.JMESPathError as exc:
        raise StacksmithConfigError(f"Invalid --tag-expr: {exc}") from exc


def _extract_tag_references(tag_expr: str) -> set[str]:
    try:
        parsed = jmespath.parser.Parser().parse(tag_expr).parsed
    except jmespath_exceptions.JMESPathError:
        return set()

    refs = set()

    def _collect(node: Any) -> None:
        if not isinstance(node, dict):
            return

        if node.get("type") == "subexpression":
            children = node.get("children", []) or []
            if (
                len(children) == 2
                and children[0].get("type") == "field"
                and children[0].get("value") == "tag"
            ):
                value = children[1].get("value")
                if isinstance(value, str):
                    refs.add(value)

        for child in node.get("children", []) or []:
            _collect(child)
        for value in node.values():
            if isinstance(value, dict):
                _collect(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        _collect(item)

    _collect(parsed)
    return refs


def _build_component_tag_context(
    stack: StackDefinition,
    component_name: str,
    component_effective_tags: set[str],
    all_stack_tags: set[str],
) -> dict[str, Any]:
    return {
        "tags": sorted(component_effective_tags),
        "tag": {tag: tag in component_effective_tags for tag in all_stack_tags},
        "component_name": component_name,
        "component_type": stack.components[component_name].type,
        "stack_name": stack.name,
        "stack_tags": sorted(stack.tags),
    }


def _evaluate_tag_expression(
    expression, context: dict[str, Any], *, component_name: str
) -> bool:
    result = expression.search(context)
    if not isinstance(result, bool):
        result_type = type(result).__name__
        raise StacksmithConfigError(
            "Tag expression must evaluate to a boolean value for every component. "
            f"Component '{component_name}' produced type '{result_type}' with value {result!r}."
        )
    return result


def _compute_stack_target_modules(
    stack: StackDefinition,
    config: ToolConfig,
    expression=None,
    referenced_tags: set[str] | None = None,
    required_tags: set[str] | None = None,
) -> list[str]:
    effective_tags_by_component = {}
    all_stack_tags = set()

    for component_name, component in stack.components.items():
        mapping = config.module_mappings.get(component.type)
        if mapping is None:
            raise StacksmithConfigError(
                f"Component '{component_name}' has type '{component.type}' "
                f"which is not defined in the tool configuration module mappings. "
                f"Available types: {', '.join(config.module_mappings.keys())}"
            )

        effective_tags = set(component.tags)
        effective_tags.update(mapping.tags)
        effective_tags_by_component[component_name] = effective_tags
        all_stack_tags.update(effective_tags)

    all_stack_tags.update(referenced_tags or set())
    required_tags = required_tags or set()

    targets = []
    for component_name in stack.components:
        effective_tags = effective_tags_by_component[component_name]
        if required_tags and not required_tags.issubset(effective_tags):
            continue

        if expression is None:
            targets.append(f"module.{component_name}")
            continue

        context = _build_component_tag_context(
            stack,
            component_name,
            effective_tags,
            all_stack_tags,
        )
        if _evaluate_tag_expression(expression, context, component_name=component_name):
            targets.append(f"module.{component_name}")

    return targets


def _resolve_stacks_for_generation(
    root: Path,
    stack_refs: Sequence[Path | str] | None,
    cache_dir: Path | None = None,
    merge_mode: str | MergeMode = MergeMode.DEEP,
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


def _validate_action_options(
    action: str | TerragruntAction,
    *,
    tags: list[str] | None,
    tag_expr: str | None,
    save_plan_json: Path | None,
    tag_support_label: str,
    save_plan_label: str,
) -> TerragruntAction:
    action_enum = TerragruntAction(action)
    if (tags or tag_expr) and action_enum not in {
        TerragruntAction.PLAN,
        TerragruntAction.APPLY,
        TerragruntAction.DESTROY,
    }:
        raise StacksmithConfigError(
            f"--tag and --tag-expr are only supported for {tag_support_label}"
        )
    if save_plan_json is not None and action_enum != TerragruntAction.PLAN:
        raise StacksmithConfigError(
            f"--save-plan-json is only supported for {save_plan_label}"
        )
    return action_enum


def _resolve_tag_targets(
    stack: StackDefinition,
    config: ToolConfig,
    *,
    tags: list[str] | None,
    tag_expr: str | None,
) -> tuple[None | object, set[str], list[str]]:
    expression = _compile_tag_expression(tag_expr) if tag_expr else None
    referenced_tags = _extract_tag_references(tag_expr) if tag_expr else None
    targets = _compute_stack_target_modules(
        stack,
        config,
        expression,
        referenced_tags=referenced_tags,
        required_tags=set(tags or []),
    )
    return expression, referenced_tags, targets


def _build_terragrunt_args(
    action: str | TerragruntAction,
    destroy: bool = False,
    targets: list[str] | None = None,
) -> list[str]:
    action_enum = TerragruntAction(action)
    terragrunt_args = [action_enum.value]
    if action_enum == TerragruntAction.PLAN and destroy:
        terragrunt_args.append("-destroy")
    for target in targets or []:
        terragrunt_args.extend(["-target", target])
    return terragrunt_args


def _resolve_stack_inputs(
    stack: StackDefinition,
    config: ToolConfig,
    vars_path: str | Sequence[str] | None,
    input_layers: Sequence[InputLayer] | None,
    cache_dir: Path | None = None,
    merge_mode: str | MergeMode = MergeMode.DEEP,
) -> dict[str, Any]:
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
        context={
            "stack": {
                "name": stack.name,
                "tags": sorted(stack.tags),
            }
        },
    )


def _generate_single_stack(
    stack: StackDefinition,
    config: ToolConfig,
    resolved_inputs: dict[str, Any],
    build_dir: Path | None,
    silent: bool = False,
    cache_dir: Path | None = None,
    use_local_modules: bool = False,
    merge_mode: str | MergeMode = MergeMode.DEEP,
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
    merge_mode: str | MergeMode = MergeMode.DEEP,
) -> tuple[Path, dict[str, Path], dict[str, StackDefinition]]:
    discovered_stacks = _resolve_stacks_for_generation(
        root,
        stack_refs,
        cache_dir=cache_dir,
        merge_mode=merge_mode,
    )
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
    LOGGER.debug(
        "Discovered stack names: {stack_names}", stack_names=sorted(stacks.keys())
    )
    stacks = filter_stacks_by_tags(
        stacks,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )
    LOGGER.debug(
        "Filtered stack names: {stack_names}", stack_names=sorted(stacks.keys())
    )
    graph = build_dependency_graph(stacks)
    order = topological_sort(graph)
    LOGGER.debug("Stack generation order: {order}", order=order)

    root_build_dir = build_dir or (root / ".stacksmith")
    if clean:
        if build_dir is not None and root_build_dir.exists():
            LOGGER.debug(
                "Cleaning existing build directory: {root_build_dir}",
                root_build_dir=root_build_dir,
            )
            shutil.rmtree(root_build_dir)
        elif stack_refs:
            for stack in stacks.values():
                if stack.source_path is None:
                    continue
                stack_build_dir = _resolve_build_dir(stack.source_path, None)
                if stack_build_dir.exists():
                    LOGGER.debug(
                        "Cleaning existing build directory: {stack_build_dir}",
                        stack_build_dir=stack_build_dir,
                    )
                    shutil.rmtree(stack_build_dir)
        elif root_build_dir.exists():
            LOGGER.debug(
                "Cleaning existing build directory: {root_build_dir}",
                root_build_dir=root_build_dir,
            )
            shutil.rmtree(root_build_dir)

    stack_build_dirs: dict[str, Path] = {}

    for name in order:
        stack = stacks[name]
        if stack_refs:
            if stack.source_path is None:
                raise RuntimeError(f"Stack '{stack.name}' is missing a source path")
            stack_out = (
                build_dir / name
                if build_dir is not None
                else _resolve_build_dir(stack.source_path, None)
            )
        else:
            relative_path = stack.source_path.parent.relative_to(root.resolve())
            stack_out = root_build_dir / relative_path
        resolved = resolved_inputs_by_stack[name]

        dep_stacks = {dep: stacks[dep] for dep in stack.depends_on}
        dep_dirs = {
            dep: stack_build_dirs[dep]
            for dep in stack.depends_on
            if dep in stack_build_dirs
        }

        write_tf_json(
            stack,
            config,
            resolved,
            stack_out,
            cache_dir=cache_dir,
            auth_config=config.remote_auth or None,
            use_local_modules=use_local_modules,
            root=None if stack_refs else root,
        )
        write_terragrunt_json(
            stack,
            config,
            resolved,
            stack_out,
            dep_stacks,
            dep_dirs,
            root=None if stack_refs else root,
        )
        stack_build_dirs[name] = stack_out

    if stack_refs:
        LOGGER.info("Generated {count} explicit stacks", count=len(stacks))
    else:
        LOGGER.info(
            "Generated {count} stacks in {root_build_dir}",
            count=len(stacks),
            root_build_dir=root_build_dir,
        )
    LOGGER.debug(
        "Stack build dirs: {stack_build_dirs}", stack_build_dirs=stack_build_dirs
    )
    return root_build_dir, stack_build_dirs, stacks


def validate_stack(
    stack_file: Path | str | Sequence[Path | str],
    *,
    config: list[str] | None = None,
    vars_file: str | Sequence[str] | None = None,
    input_layers: Sequence[InputLayer] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    merge_mode: str | MergeMode = MergeMode.DEEP,
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
        cache_dir, config_paths, loaded_config = _load_runtime_config(
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


def diagnose_cache(
    stack_file: Path | str | Sequence[Path | str],
    *,
    config: list[str] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    merge_mode: str | MergeMode = MergeMode.DEEP,
) -> int:
    """Display stacksmith cache and vendor diagnostics."""
    payload = inspect_cache_diagnostics(
        stack_file,
        config=config,
        build_dir=build_dir,
        no_cache=no_cache,
        merge_mode=merge_mode,
    )
    _render_cache_diagnostics_to_stderr(payload)
    return 0


def inspect_cache_diagnostics(
    stack_file: Path | str | Sequence[Path | str],
    *,
    config: list[str] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    merge_mode: str | MergeMode = MergeMode.DEEP,
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
    cache_dir, config_paths, _ = _load_runtime_config(
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


def _render_cache_diagnostics_to_stderr(payload: dict[str, Any]) -> None:
    print_to_stderr("Stacksmith diagnostics")
    print_to_stderr("======================")
    print_to_stderr(f"Stack file: {payload['stack_file']}")
    print_to_stderr(f"Config paths: {', '.join(payload['config_paths'])}")
    print_to_stderr(f"Build directory: {payload['build_directory']}")
    print_to_stderr(f"Remote cache directory: {payload['remote_cache_directory']}")

    remote_cache_entries = payload.get("remote_cache_entries", [])
    if payload.get("remote_cache_exists", False):
        print_to_stderr("Remote cache contents:")
        for entry in remote_cache_entries:
            print_to_stderr(f"  {entry['name']} ({entry['type']})")
    else:
        print_to_stderr("Remote cache not found.")

    print_to_stderr(f"Vendor directory: {payload['vendor_directory']}")
    vendored_modules = payload.get("vendored_modules", [])
    vendor_manifest_path = payload.get("vendor_manifest_path")
    if payload.get("vendor_directory_exists", False):
        if vendor_manifest_path:
            print_to_stderr(f"Vendor manifest: {vendor_manifest_path}")
            print_to_stderr(f"Vendored modules: {len(vendored_modules)}")
            for item in vendored_modules:
                print_to_stderr(
                    f"  {item['key']}: {item['source']} @ {item['version']}"
                )
        else:
            print_to_stderr("Vendor manifest not found.")
        if payload["vendor_directories"]:
            print_to_stderr("Vendored module directories:")
            for name in payload["vendor_directories"]:
                print_to_stderr(f"  {name}")
        else:
            print_to_stderr("No vendored module directories found.")
    else:
        print_to_stderr("Vendor directory not found.")


def generate_stack(
    stack_file: Path | str | Sequence[Path | str],
    *,
    config: list[str] | None = None,
    vars_file: str | Sequence[str] | None = None,
    input_layers: Sequence[InputLayer] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    use_local_modules: bool = False,
    merge_mode: str | MergeMode = MergeMode.DEEP,
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

    Returns:
        Output directory path containing generated files.
    """
    cache_dir, config_paths, loaded_config = _load_runtime_config(
        config,
        build_dir,
        no_cache=no_cache,
        merge_mode=merge_mode,
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


def run_stack_operation(
    stack_file: Path | str | Sequence[Path | str],
    operation_name: str,
    *,
    config: list[str] | None = None,
    vars_file: str | Sequence[str] | None = None,
    input_layers: Sequence[InputLayer] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    no_cas: bool = False,
    force_rerun: bool = False,
    merge_mode: str | MergeMode = MergeMode.DEEP,
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
    cache_dir, _, loaded_config = _load_runtime_config(
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
    *,
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
    strict_validation_warnings: bool = False,
    fail_on_changes: bool = False,
    no_cas: bool = False,
    merge_mode: str | MergeMode = MergeMode.DEEP,
    validation_report_format: (
        str | ValidationReportFormat
    ) = ValidationReportFormat.JSON,
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

    Returns:
        Process-style exit code from the Terragrunt action.
    """
    cache_dir, config_paths, loaded_config = _load_runtime_config(
        config,
        build_dir,
        no_cache=no_cache,
        merge_mode=merge_mode,
    )
    effective_no_cas = no_cas or no_cache
    if no_cache and not no_cas:
        LOGGER.warning(
            "--no-cache now also disables Terragrunt CAS for runtime commands. "
            "Use --no-cas for CAS-only control."
        )
    action_enum = _validate_action_options(
        action,
        tags=tags,
        tag_expr=tag_expr,
        save_plan_json=save_plan_json,
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
        _, _, targets = _resolve_tag_targets(
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
        _build_terragrunt_args(action_enum, destroy, targets=targets),
        output_dir,
        auto_approve=auto_approve,
        config=loaded_config,
        stack_name=stack.name,
        cache_dir=cache_dir,
        auth_config=loaded_config.remote_auth or None,
        save_plan_json=save_plan_json,
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


def run_all_stacks(
    action: str | TerragruntAction,
    root: Path,
    *,
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
    strict_validation_warnings: bool = False,
    fail_on_changes: bool = False,
    no_cas: bool = False,
    stacks: Sequence[Path | str] | None = None,
    merge_mode: str | MergeMode = MergeMode.DEEP,
    validation_report_format: (
        str | ValidationReportFormat
    ) = ValidationReportFormat.JSON,
) -> int:
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

    Returns:
        Process-style exit code from the Terragrunt action.
    """
    cache_dir, config_paths, loaded_config = _load_runtime_config(
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
    action_enum = _validate_action_options(
        action,
        tags=tags,
        tag_expr=tag_expr,
        save_plan_json=save_plan_json,
        tag_support_label="run-all plan, apply, and destroy",
        save_plan_label="run-all plan",
    )

    LOGGER.debug(
        "Running run-all action {action} from root {root} with config paths: {config_paths}",
        action=action_enum.value,
        root=root,
        config_paths=config_paths,
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
            _, _, targets = _resolve_tag_targets(
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
            stack_args_by_name[stack_name] = _build_terragrunt_args(
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
        _build_terragrunt_args(action_enum, destroy),
        stack_build_dirs,
        auto_approve=auto_approve,
        config=loaded_config,
        cache_dir=cache_dir,
        auth_config=loaded_config.remote_auth or None,
        stack_args_by_name=stack_args_by_name,
        save_plan_json=save_plan_json,
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
    *,
    config: list[str] | None = None,
    component_types: list[str] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    merge_mode: str | MergeMode = MergeMode.DEEP,
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
    cache_dir, config_paths, loaded_config = _load_runtime_config(
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
    plan_policy_results = inspect_plan_policies(loaded_config, config_locations)
    return component_results, plan_policy_results
