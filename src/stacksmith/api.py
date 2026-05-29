import csv
import io
import json
import re
import shutil
import sys
from collections.abc import Sequence
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
from .enums import TerragruntAction, ValidationReportFormat, ValidationRowType
from .exceptions import StacksmithConfigError, StacksmithError
from .generator import write_tf_json
from .inspector import (
    PlanPolicyInfo,
    ResourceTypeInfo,
    inspect_all,
    inspect_plan_policies,
)
from .loader import load_config, load_config_with_locations, load_stack
from .models import RemoteAuthConfig, StackDefinition, ToolConfig
from .remote import is_remote_url, resolve_remote
from .runner import run_terragrunt, run_terragrunt_all_ordered
from .terragrunt import write_terragrunt_json
from .utils import stacksmith_env_list
from .validation import PlanValidationResult
from .validations.outcomes import PlanValidationOutcome
from .variables import InputLayer, resolve_inputs
from .vendor import get_vendor_dir, load_vendor_manifest


def _default_config_paths() -> list[str]:
    if config_env := stacksmith_env_list("CONFIG"):
        return config_env
    return [str(Path.cwd() / "stacksmith-config.yaml")]


def _resolve_config_paths(
    config_args: list[str] | None,
    cache_dir: Path | None = None,
) -> list[Path]:
    raw_paths = config_args if config_args else _default_config_paths()
    resolved: list[Path] = []
    for ref in raw_paths:
        if is_remote_url(ref):
            if cache_dir is None:
                raise StacksmithConfigError(
                    f"Cannot fetch remote config without a cache directory: {ref}"
                )
            resolved.append(resolve_remote(ref, cache_dir))
        else:
            resolved.append(Path(ref).expanduser())
    LOGGER.debug("Resolved config paths: {paths}", paths=resolved)
    return resolved


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


_VALIDATION_REPORT_CSV_COLUMNS = (
    "row_type",
    "command",
    "report_status",
    "exit_code",
    "strict_validation_warnings",
    "stack_count",
    "summary_pass",
    "summary_warn",
    "summary_fail",
    "stack_name",
    "result_name",
    "result_status",
    "result_message",
    "result_detail_json",
)


def _split_validation_message(message: str) -> tuple[str, str | None]:
    summary, separator, detail = message.partition(" — ")
    if separator:
        return summary, detail
    return message, None


def _validation_report_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    report_row = {
        "row_type": ValidationRowType.REPORT.value,
        "command": report.get("command", ""),
        "report_status": report.get("status", ""),
        "exit_code": report.get("exit_code", ""),
        "strict_validation_warnings": report.get("strict_validation_warnings", ""),
        "stack_count": report.get("stack_count", ""),
        "summary_pass": summary.get("pass", ""),
        "summary_warn": summary.get("warn", ""),
        "summary_fail": summary.get("fail", ""),
        "stack_name": report.get("stack_name", ""),
        "result_name": "",
        "result_status": "",
        "result_message": "",
        "result_detail_json": "",
    }
    raw_results = report.get("results")
    results = raw_results if isinstance(raw_results, list) else []

    rows: list[dict[str, Any]] = [report_row]
    if not results:
        return rows

    for raw_result in results:
        if isinstance(raw_result, dict):
            result_stack_name = raw_result.get(
                "stack_name", report.get("stack_name", "")
            )
            result_name = raw_result.get("name", "")
            result_status = raw_result.get("status", "")
            result_message = raw_result.get("message", "")
            result_message, result_detail = _split_validation_message(result_message)
        else:
            result_stack_name = report.get("stack_name", "")
            result_name = ""
            result_status = ""
            result_message = json.dumps(raw_result, sort_keys=True)
            result_detail = None

        rows.append(
            {
                "row_type": ValidationRowType.RESULT.value,
                "command": "",
                "report_status": "",
                "exit_code": "",
                "strict_validation_warnings": "",
                "stack_count": "",
                "summary_pass": "",
                "summary_warn": "",
                "summary_fail": "",
                "stack_name": result_stack_name,
                "result_name": result_name,
                "result_status": result_status,
                "result_message": result_message,
                "result_detail_json": (
                    json.dumps({"detail": result_detail}, sort_keys=True)
                    if result_detail is not None
                    else ""
                ),
            }
        )

    return rows


def _emit_validation_report(
    report: dict[str, Any],
    *,
    report_format: str | ValidationReportFormat = ValidationReportFormat.JSON,
) -> None:
    resolved_format = ValidationReportFormat(report_format)
    if resolved_format == ValidationReportFormat.CSV:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=_VALIDATION_REPORT_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(_validation_report_rows(report))
        print(buffer.getvalue(), end="")
        return

    print(json.dumps(report, sort_keys=True))


def _emit_human_output(message: str) -> None:
    print(message, file=sys.stderr)


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
) -> tuple[Path, list[Path], ToolConfig]:
    cache_dir = _resolve_cache_dir(build_dir, base_dir)
    if no_cache:
        _clean_cache(cache_dir)
    config_paths = _resolve_config_paths(config, cache_dir=cache_dir)
    return cache_dir, config_paths, load_config(config_paths)


def _compile_tag_expression(tag_expr: str):
    normalized = re.sub(
        r"tag\[\s*'([^']+)'\s*\]",
        r'tag."\1"',
        tag_expr,
    )
    normalized = re.sub(
        r'tag\[\s*"([^\"]+)"\s*\]',
        r'tag."\1"',
        normalized,
    )
    try:
        return jmespath.compile(normalized)
    except jmespath_exceptions.JMESPathError as exc:
        raise StacksmithConfigError(f"Invalid --tag-expr: {exc}") from exc


def _extract_tag_references(tag_expr: str) -> set[str]:
    dot_style = re.findall(r"\btag\.([A-Za-z_][A-Za-z0-9_]*)\b", tag_expr)
    quoted_style = re.findall(r'tag\."([^"]+)"', tag_expr)
    bracket_matches = re.findall(
        r"tag\[\s*'([^']+)'\s*\]|tag\[\s*\"([^\"]+)\"\s*\]",
        tag_expr,
    )
    bracket_style = {item for match in bracket_matches for item in match if item}
    return set(dot_style).union(quoted_style, bracket_style)


def _build_resource_tag_context(
    stack: StackDefinition,
    resource_name: str,
    resource_effective_tags: set[str],
    all_stack_tags: set[str],
) -> dict[str, Any]:
    return {
        "tags": sorted(resource_effective_tags),
        "tag": {tag: tag in resource_effective_tags for tag in all_stack_tags},
        "resource_name": resource_name,
        "resource_type": stack.components[resource_name].type,
        "stack_name": stack.name,
        "stack_tags": sorted(stack.tags),
    }


def _evaluate_tag_expression(
    expression,
    context: dict[str, Any],
    *,
    resource_name: str,
) -> bool:
    result = expression.search(context)
    if not isinstance(result, bool):
        result_type = type(result).__name__
        raise StacksmithConfigError(
            "Tag expression must evaluate to a boolean value for every resource. "
            f"Resource '{resource_name}' produced type '{result_type}' with value {result!r}."
        )
    return result


def _compute_stack_target_modules(
    stack: StackDefinition,
    config: ToolConfig,
    expression=None,
    referenced_tags: set[str] | None = None,
    required_tags: set[str] | None = None,
) -> list[str]:
    effective_tags_by_resource: dict[str, set[str]] = {}
    all_stack_tags: set[str] = set()

    for resource_name, resource in stack.components.items():
        mapping = config.module_mappings.get(resource.type)
        if mapping is None:
            raise StacksmithConfigError(
                f"Component '{resource_name}' has type '{resource.type}' "
                f"which is not defined in the tool configuration module mappings. "
                f"Available types: {', '.join(config.module_mappings.keys())}"
            )

        effective_tags = set(resource.tags)
        effective_tags.update(mapping.tags)
        effective_tags_by_resource[resource_name] = effective_tags
        all_stack_tags.update(effective_tags)

    all_stack_tags.update(referenced_tags or set())
    required_tags = required_tags or set()

    targets: list[str] = []
    for resource_name in stack.components:
        effective_tags = effective_tags_by_resource[resource_name]
        if required_tags and not required_tags.issubset(effective_tags):
            continue

        if expression is None:
            targets.append(f"module.{resource_name}")
            continue

        context = _build_resource_tag_context(
            stack,
            resource_name,
            effective_tags,
            all_stack_tags,
        )
        if _evaluate_tag_expression(expression, context, resource_name=resource_name):
            targets.append(f"module.{resource_name}")

    return targets


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


def _generate_single_stack(
    stack_path: Path,
    config_paths: list[Path],
    vars_path: str | Sequence[str] | None,
    input_layers: Sequence[InputLayer] | None,
    build_dir: Path | None,
    silent: bool = False,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    use_local_modules: bool = False,
) -> tuple[Path, int]:
    LOGGER.debug("Loading config from paths: {config_paths}", config_paths=config_paths)
    config = load_config(config_paths)
    stack = load_stack(_find_stack_file(stack_path))
    LOGGER.debug(
        "Loaded stack {name} from {path}", name=stack.name, path=stack.source_path
    )
    resolved = resolve_inputs(
        vars_file=vars_path,
        input_layers=input_layers,
        config_validations=config.var_validations or None,
        config_validation_base_path=(
            config.source_path.parent if config.source_path is not None else None
        ),
        cache_dir=cache_dir,
        auth_config=auth_config,
    )
    LOGGER.debug("Resolved variable keys: {keys}", keys=sorted(resolved.keys()))
    output_dir = _resolve_build_dir(stack_path.resolve(), build_dir)

    write_tf_json(
        stack,
        config,
        resolved,
        output_dir,
        cache_dir=cache_dir,
        auth_config=auth_config,
        use_local_modules=use_local_modules,
    )
    write_terragrunt_json(stack, config, resolved, output_dir)

    if not silent:

        LOGGER.info("Generated files in {output_dir}", output_dir=output_dir)
    return output_dir, 0


def _generate_all_stacks(
    root: Path,
    config_paths: list[Path],
    vars_path: str | Sequence[str] | None,
    input_layers: Sequence[InputLayer] | None,
    build_dir: Path | None,
    clean: bool = False,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
    use_local_modules: bool = False,
) -> tuple[Path, dict[str, Path], dict[str, StackDefinition]]:
    LOGGER.debug("Loading config from paths: {config_paths}", config_paths=config_paths)
    config = load_config(config_paths)
    stacks = discover_stacks(root)
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
    if clean and root_build_dir.exists():
        LOGGER.debug(
            "Cleaning existing build directory: {root_build_dir}",
            root_build_dir=root_build_dir,
        )
        shutil.rmtree(root_build_dir)

    stack_build_dirs: dict[str, Path] = {}

    for name in order:
        stack = stacks[name]
        relative_path = stack.source_path.parent.relative_to(root.resolve())
        stack_out = root_build_dir / relative_path
        resolved = resolve_inputs(
            vars_file=vars_path,
            input_layers=input_layers,
            config_validations=config.var_validations or None,
            config_validation_base_path=(
                config.source_path.parent if config.source_path is not None else None
            ),
            cache_dir=cache_dir,
            auth_config=auth_config,
        )

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
            auth_config=auth_config,
            use_local_modules=use_local_modules,
            root=root,
        )
        write_terragrunt_json(
            stack, config, resolved, stack_out, dep_stacks, dep_dirs, root=root
        )
        stack_build_dirs[name] = stack_out

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
    stack_file: Path,
    *,
    config: list[str] | None = None,
    vars_file: str | Sequence[str] | None = None,
    input_layers: Sequence[InputLayer] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    strict_validation_warnings: bool = False,
    validation_report_format: (
        str | ValidationReportFormat
    ) = ValidationReportFormat.JSON,
) -> int:
    """Validate a stack definition and its resolved variables.

    Args:
        stack_file: Path to the stack definition file.
        config: Optional config file paths or URLs. Later entries override earlier ones.
        vars_file: Optional vars file path, URL, or ordered sequence of vars
            file paths or URLs.
        input_layers: Optional ordered CLI input layers merged in call order.
        build_dir: Optional build directory used to derive the cache directory.
        no_cache: When `True`, clear the remote cache before resolving resources.
        strict_validation_warnings: Present for report parity across commands.
        validation_report_format: Format used for machine-readable validation
            report output.

    Returns:
        Process-style exit code. Returns `0` when validation succeeds.
    """
    try:
        cache_dir, config_paths, loaded_config = _load_runtime_config(
            config,
            build_dir,
            no_cache=no_cache,
        )
        load_stack(_find_stack_file(stack_file))
        LOGGER.debug(
            "Validating stack {stack_file} using config paths: {config_paths}",
            stack_file=stack_file,
            config_paths=config_paths,
        )
        resolve_inputs(
            vars_file=vars_file,
            input_layers=input_layers,
            config_validations=loaded_config.var_validations or None,
            config_validation_base_path=(
                loaded_config.source_path.parent
                if loaded_config.source_path is not None
                else None
            ),
            cache_dir=cache_dir,
            auth_config=loaded_config.remote_auth or None,
        )
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
        _emit_validation_report(
            _build_validate_report(exit_code=1, message=message),
            report_format=validation_report_format,
        )
        return 1

    LOGGER.info("Validation passed.")
    _emit_validation_report(
        _build_validate_report(exit_code=0, message="Validation passed"),
        report_format=validation_report_format,
    )
    return 0


def diagnose_cache(
    stack_file: Path,
    *,
    config: list[str] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
) -> int:
    """Display stacksmith cache and vendor diagnostics."""
    cache_dir, config_paths, loaded_config = _load_runtime_config(
        config,
        build_dir,
        no_cache=no_cache,
    )
    stack = load_stack(_find_stack_file(stack_file))
    build_dir_resolved = _resolve_build_dir(stack_file.resolve(), build_dir)

    _emit_human_output("Stacksmith diagnostics")
    _emit_human_output("======================")
    _emit_human_output(f"Stack file: {stack.source_path or stack_file}")
    _emit_human_output(f"Config paths: {', '.join(str(path) for path in config_paths)}")
    _emit_human_output(f"Build directory: {build_dir_resolved}")
    _emit_human_output(f"Remote cache directory: {cache_dir}")

    if cache_dir.exists():
        _emit_human_output("Remote cache contents:")
        for entry in sorted(cache_dir.iterdir()):
            _emit_human_output(
                f"  {entry.name} ({'dir' if entry.is_dir() else 'file'})"
            )
    else:
        _emit_human_output("Remote cache not found.")

    vendor_dir = get_vendor_dir()
    _emit_human_output(f"Vendor directory: {vendor_dir}")
    if vendor_dir.exists():
        manifest_path = vendor_dir / "vendor-manifest.json"
        if manifest_path.exists():
            manifest = load_vendor_manifest(vendor_dir)
            _emit_human_output(f"Vendor manifest: {manifest_path}")
            _emit_human_output(f"Vendored modules: {len(manifest)}")
            for key, item in manifest.items():
                _emit_human_output(f"  {key}: {item['source']} @ {item['version']}")
        else:
            _emit_human_output("Vendor manifest not found.")
        vendor_dirs = [p for p in vendor_dir.iterdir() if p.is_dir()]
        if vendor_dirs:
            _emit_human_output("Vendored module directories:")
            for entry in sorted(vendor_dirs):
                _emit_human_output(f"  {entry.name}")
        else:
            _emit_human_output("No vendored module directories found.")
    else:
        _emit_human_output("Vendor directory not found.")

    return 0


def generate_stack(
    stack_file: Path,
    *,
    config: list[str] | None = None,
    vars_file: str | Sequence[str] | None = None,
    input_layers: Sequence[InputLayer] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
    use_local_modules: bool = False,
) -> int:
    """Generate Terraform and Terragrunt files for a single stack.

    Args:
        stack_file: Path to the stack definition file.
        config: Optional config file paths or URLs. Later entries override earlier ones.
        vars_file: Optional vars file path, URL, or ordered sequence of vars
            file paths or URLs.
        input_layers: Optional ordered CLI input layers merged in call order.
        build_dir: Optional output directory for generated files.
        no_cache: When `True`, clear the remote cache before resolving resources.
        use_local_modules: When `True`, rewrite module sources to local vendored paths.

    Returns:
        Process-style exit code from generation.
    """
    cache_dir, config_paths, loaded_config = _load_runtime_config(
        config,
        build_dir,
        no_cache=no_cache,
    )
    LOGGER.debug(
        "Generating stack {stack_file} with config paths: {config_paths}",
        stack_file=stack_file,
        config_paths=config_paths,
    )
    _, exit_code = _generate_single_stack(
        stack_file,
        config_paths,
        vars_file,
        input_layers,
        build_dir,
        cache_dir=cache_dir,
        auth_config=loaded_config.remote_auth or None,
        use_local_modules=use_local_modules,
    )
    return exit_code


def run_stack_action(
    action: str | TerragruntAction,
    stack_file: Path,
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
    validation_report_format: (
        str | ValidationReportFormat
    ) = ValidationReportFormat.JSON,
) -> int:
    """Generate files for a stack and run a Terragrunt action.

    Args:
        action: Terragrunt action to execute.
        stack_file: Path to the stack definition file.
        config: Optional config file paths or URLs. Later entries override earlier ones.
        vars_file: Optional vars file path, URL, or ordered sequence of vars
            file paths or URLs.
        input_layers: Optional ordered CLI input layers merged in call order.
        build_dir: Optional output directory for generated files.
        no_cache: When `True`, clear the remote cache before resolving resources.
        auto_approve: When `True`, pass `--auto-approve` to apply and destroy.
        destroy: When `True` and `action` is `plan`, generate a destroy plan.
        use_local_modules: When `True`, rewrite module sources to local vendored paths.
        tags: Optional list of tags used to select resource targets. All listed
            tags must be present on a resource for it to match.
        tag_expr: Optional JMESPath expression used to select module targets.
        save_plan_json: Optional file or directory path used to persist rendered
            plan JSON output for plan actions.
        strict_validation_warnings: When `True`, warning outcomes from plan
            validations are treated as failures.
        validation_report_format: Format used for machine-readable validation
            report output.

    Returns:
        Process-style exit code from the Terragrunt action.
    """
    cache_dir, config_paths, loaded_config = _load_runtime_config(
        config,
        build_dir,
        no_cache=no_cache,
    )
    action_enum = TerragruntAction(action)
    if (tags or tag_expr) and action_enum not in {
        TerragruntAction.PLAN,
        TerragruntAction.APPLY,
        TerragruntAction.DESTROY,
    }:
        raise StacksmithConfigError(
            "--tag and --tag-expr are only supported for plan, apply, and destroy"
        )
    if save_plan_json is not None and action_enum != TerragruntAction.PLAN:
        raise StacksmithConfigError("--save-plan-json is only supported for plan")

    LOGGER.debug(
        "Running terragrunt action {action} for stack {stack_file} with config paths: {config_paths}",
        action=action_enum.value,
        stack_file=stack_file,
        config_paths=config_paths,
    )
    stack = load_stack(_find_stack_file(stack_file))
    plan_validation_results: list[PlanValidationResult] = []
    targets: list[str] | None = None
    if tags or tag_expr:
        expression = _compile_tag_expression(tag_expr) if tag_expr else None
        referenced_tags = _extract_tag_references(tag_expr) if tag_expr else None
        targets = _compute_stack_target_modules(
            stack,
            loaded_config,
            expression,
            referenced_tags=referenced_tags,
            required_tags=set(tags or []),
        )
        if not targets:
            LOGGER.error(
                "No resources in stack '{stack_name}' matched tag selectors",
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

    output_dir, exit_code = _generate_single_stack(
        stack_file,
        config_paths,
        vars_file,
        input_layers,
        build_dir,
        silent=True,
        cache_dir=cache_dir,
        auth_config=loaded_config.remote_auth or None,
        use_local_modules=use_local_modules,
    )
    if exit_code != 0:
        if action_enum == TerragruntAction.PLAN:
            _emit_validation_report(
                _build_plan_validation_report(
                    command=TerragruntAction.PLAN.value,
                    exit_code=exit_code,
                    strict_validation_warnings=strict_validation_warnings,
                    results=plan_validation_results,
                    stack_name=stack.name,
                    stack_count=1,
                ),
                report_format=validation_report_format,
            )
        return exit_code
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
        plan_validation_results=plan_validation_results,
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
        no_cache: When `True`, clear the remote cache before resolving resources.
        include_tags: Optional tags used to include matching stacks.
        exclude_tags: Optional tags used to exclude matching stacks.
        clean: When `True`, remove the build directory before generation.
        auto_approve: When `True`, pass `--auto-approve` to apply and destroy.
        destroy: When `True` and `action` is `plan`, generate a destroy plan.
        use_local_modules: When `True`, rewrite module sources to local vendored paths.
        tags: Optional list of tags used to select resource targets. All listed
            tags must be present on a resource for it to match.
        tag_expr: Optional JMESPath expression used to select module targets.
        save_plan_json: Optional directory used to persist rendered plan JSON
            output for each stack during plan actions.
        strict_validation_warnings: When `True`, warning outcomes from plan
            validations are treated as failures.
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
    )
    action_enum = TerragruntAction(action)
    if (tags or tag_expr) and action_enum not in {
        TerragruntAction.PLAN,
        TerragruntAction.APPLY,
        TerragruntAction.DESTROY,
    }:
        raise StacksmithConfigError(
            "--tag and --tag-expr are only supported for run-all plan, apply, and destroy"
        )
    if save_plan_json is not None and action_enum != TerragruntAction.PLAN:
        raise StacksmithConfigError(
            "--save-plan-json is only supported for run-all plan"
        )

    LOGGER.debug(
        "Running run-all action {action} from root {root} with config paths: {config_paths}",
        action=action_enum.value,
        root=root,
        config_paths=config_paths,
    )
    _, stack_build_dirs, stacks = _generate_all_stacks(
        root,
        config_paths,
        vars_file,
        input_layers,
        build_dir,
        clean=clean,
        cache_dir=cache_dir,
        auth_config=loaded_config.remote_auth or None,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
        use_local_modules=use_local_modules,
    )

    stack_args_by_name: dict[str, list[str]] | None = None
    plan_validation_results: list[PlanValidationResult] = []
    if tags or tag_expr:
        expression = _compile_tag_expression(tag_expr) if tag_expr else None
        referenced_tags = _extract_tag_references(tag_expr) if tag_expr else None
        filtered_stack_dirs: dict[str, Path] = {}
        stack_args_by_name = {}
        for stack_name, stack_dir in stack_build_dirs.items():
            targets = _compute_stack_target_modules(
                stacks[stack_name],
                loaded_config,
                expression,
                referenced_tags=referenced_tags,
                required_tags=set(tags or []),
            )
            if not targets:
                LOGGER.info(
                    "Skipping stack '{stack_name}': no resources matched tag selectors",
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
        plan_validation_results=plan_validation_results,
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
    resource_types: list[str] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
) -> tuple[list[ResourceTypeInfo], list[PlanPolicyInfo]]:
    """Inspect configured modules and return variable/mapping metadata.

    Args:
        config: Optional config file paths or URLs.
        resource_types: Specific resource types to inspect; inspects all when `None`.
        build_dir: Optional build directory used to derive the cache directory.
        no_cache: When `True`, clear the remote cache before resolving resources.

    Returns:
        Tuple of resource inspection results and plan policy inspection results.
    """
    cache_dir, config_paths, loaded_config = _load_runtime_config(
        config,
        build_dir,
        no_cache=no_cache,
    )
    _, config_locations = load_config_with_locations(config_paths)
    resource_results = inspect_all(
        loaded_config,
        resource_types=resource_types,
        cache_dir=cache_dir,
        auth_config=loaded_config.remote_auth or None,
        config_locations=config_locations,
    )
    plan_policy_results = inspect_plan_policies(loaded_config, config_locations)
    return resource_results, plan_policy_results


def inspect_modules_context(
    *,
    config: list[str] | None = None,
    resource_types: list[str] | None = None,
    build_dir: Path | None = None,
    no_cache: bool = False,
) -> tuple[Path, ToolConfig, list[ResourceTypeInfo], list[PlanPolicyInfo]]:
    cache_dir, config_paths, loaded_config = _load_runtime_config(
        config,
        build_dir,
        no_cache=no_cache,
    )
    _, config_locations = load_config_with_locations(config_paths)
    resource_results = inspect_all(
        loaded_config,
        resource_types=resource_types,
        cache_dir=cache_dir,
        auth_config=loaded_config.remote_auth or None,
        config_locations=config_locations,
    )
    plan_policy_results = inspect_plan_policies(loaded_config, config_locations)
    return cache_dir, loaded_config, resource_results, plan_policy_results
