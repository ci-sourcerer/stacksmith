import argparse
import json
import logging
import sys
from collections.abc import Callable
from importlib.metadata import version as metadata_version
from pathlib import Path

from loguru import logger as LOGGER
from stacksmith.cli.args import (
    _add_common_args,
    _add_plan_output_args,
    _add_stack_arg,
    _add_target_selection_args,
    _add_validation_report_format_arg,
    _configure_ci_validate_parser,
    _configure_diagnose_parser,
    _configure_info_environments_parser,
    _configure_inspect_parser,
    _path_type,
    get_default_run_file,
    get_default_stack_refs,
)
from stacksmith.enums import MergeMode
from stacksmith.loader import load_runfiles
from stacksmith.models import FileReference
from stacksmith.remote import is_remote_url, resolve_if_remote
from stacksmith.utils import stacksmith_env

from ..api import (
    generate_stack,
    inspect_cache_diagnostics,
    inspect_environments,
    inspect_modules,
    run_all_stacks,
    run_stack_action,
    run_stack_operation,
    validate_ci_inputs,
    validate_stack,
)
from ..enums import InspectOutputFormat, TerragruntAction, ValidationReportFormat
from ..exceptions import StacksmithError
from ..inspector import format_json, format_table
from ..utils import load_env_files
from .args import (
    get_env_file_paths,
    is_debug_enabled,
    is_quiet_enabled,
    parse_input_layers,
)


def _make_category_filter(name: str, root_level_no: int):
    def _filter(record: dict) -> bool:
        if (
            (record.get("extra") or {}).get("logger_name") or record.get("name")
        ) != name:
            return False
        return record.get("level", {}).get("no", 0) < root_level_no

    return _filter


class _InterceptHandler(logging.Handler):
    def __init__(self, category_levels: dict[str, int] | None, root_level: int):
        super().__init__()
        self._category_levels = category_levels or {}
        self._root_level = root_level

    def emit(self, record: logging.LogRecord) -> None:
        effective = self._category_levels.get(record.name, self._root_level)
        if record.levelno < effective:
            return

        try:
            level = LOGGER.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        LOGGER.bind(logger_name=record.name).opt(
            depth=depth, exception=record.exc_info
        ).log(level, record.getMessage())


def _configure_logging(
    debug: bool = False,
    quiet: bool = False,
    category_levels: dict[str, int] | None = None,
) -> None:
    if category_levels is None:
        category_levels = {}

    # Root/global threshold
    if quiet:
        root_level = logging.ERROR
    elif debug:
        root_level = logging.DEBUG
    else:
        root_level = logging.INFO

    # Primary sink enforces the global/root threshold
    LOGGER.remove()
    LOGGER.add(
        sys.stderr,
        level=logging.getLevelName(root_level),
        colorize=True,
        backtrace=debug,
        diagnose=debug,
        format="<green>{time:HH:mm:ss.SSS}</green> <level>{level: <6}</level> {message}",
    )

    # For categories that request more-verbose levels than the root, add
    # dedicated sinks that only allow records for that category and only for
    # levels below the root threshold (to avoid duplicate output).
    for cat_name, cat_level in (category_levels or {}).items():
        if cat_level >= root_level:
            # No special sink needed; _InterceptHandler will drop below-threshold
            # records for this category.
            continue

        LOGGER.add(
            sys.stderr,
            level=logging.getLevelName(cat_level),
            filter=_make_category_filter(cat_name, root_level),
            colorize=True,
            backtrace=debug,
            diagnose=debug,
            format="<green>{time:HH:mm:ss.SSS}</green> <level>{level: <6}</level> {message}",
        )

    # Forward standard-library logging into Loguru using a dedicated handler
    # that knows about category level overrides.
    logging.basicConfig(
        handlers=[_InterceptHandler(category_levels, root_level)], level=0
    )


def _ordered_input_layers(
    args: argparse.Namespace,
) -> list[tuple[str, object]] | None:
    return parse_input_layers(getattr(args, "input_layers", None))


def _runfile_cache_dir(args: argparse.Namespace) -> Path:
    build_dir = getattr(args, "build_dir", None)
    if build_dir is not None:
        return build_dir / ".cache"

    if getattr(args, "command", None) == "run-all":
        return getattr(args, "root", Path.cwd()) / ".stacksmith" / ".cache"
    return Path.cwd() / ".stacksmith" / ".cache"


def _load_runfile_if_present(args: argparse.Namespace):
    run_file_refs = list(getattr(args, "runfile", None) or [])
    if not run_file_refs:
        default_run_file = get_default_run_file()
        if default_run_file:
            run_file_refs = [default_run_file]

    if not run_file_refs:
        return None

    resolved_paths = []
    for run_file_ref in run_file_refs:
        if is_remote_url(run_file_ref):
            resolved_paths.append(
                resolve_if_remote(run_file_ref, _runfile_cache_dir(args))
            )
        else:
            resolved_paths.append(Path(run_file_ref).expanduser())

    return load_runfiles(resolved_paths)


def _apply_runfile(args: argparse.Namespace) -> argparse.Namespace:
    if getattr(args, "_runfile_applied", False):
        return args

    runfile = _load_runfile_if_present(args)
    if runfile is None:
        args._runfile_applied = True
        return args

    if getattr(args, "merge_mode", None) is None and runfile.merge_mode is not None:
        args.merge_mode = runfile.merge_mode.value

    args.config = [*runfile.configs, *(getattr(args, "config", None) or [])] or None

    run_layers = [("vars", item) for item in runfile.vars]
    run_layers.extend(
        ("var", f"{name}={json.dumps(value)}") for name, value in runfile.var.items()
    )
    if run_layers:
        args.input_layers = run_layers + list(getattr(args, "input_layers", None) or [])

    if hasattr(args, "stack"):
        stack_refs = [*runfile.stacks, *(getattr(args, "stack", None) or [])]
        args.stack = stack_refs or None

    args._runfile_applied = True
    return args


def _stack_arg(
    args: argparse.Namespace,
) -> Path | str | FileReference | list[Path | str | FileReference]:
    _apply_runfile(args)
    stack_refs: list[Path | str | FileReference] = list(
        getattr(args, "stack", None) or []
    )
    if getattr(args, "stack_file", None) is not None:
        stack_refs.append(args.stack_file)
    if not stack_refs:
        stack_refs = get_default_stack_refs()
    return stack_refs[0] if len(stack_refs) == 1 else stack_refs


def _run_all_stack_args(
    args: argparse.Namespace,
) -> list[Path | str | FileReference] | None:
    _apply_runfile(args)
    stack_refs = list(getattr(args, "stack", None) or [])
    return stack_refs or None


def _merge_mode_arg(args: argparse.Namespace) -> MergeMode:
    _apply_runfile(args)
    return MergeMode(getattr(args, "merge_mode", None) or MergeMode.DEEP.value)


def _vars_arg(args: argparse.Namespace) -> list[str] | None:
    raw_layers = getattr(args, "input_layers", None)
    if raw_layers and any(kind == "vars" for kind, _ in raw_layers):
        return []
    return getattr(args, "vars_file", None)


def _validation_report_format(args: argparse.Namespace) -> ValidationReportFormat:
    return ValidationReportFormat(
        getattr(args, "validation_report_format", ValidationReportFormat.JSON.value)
    )


def _cmd_validate(args: argparse.Namespace) -> int:
    _apply_runfile(args)
    report = validate_stack(
        _stack_arg(args),
        config=args.config,
        vars_file=_vars_arg(args),
        input_layers=_ordered_input_layers(args),
        build_dir=args.build_dir,
        no_cache=args.no_cache,
        merge_mode=_merge_mode_arg(args),
        validation_report_format=_validation_report_format(args),
    )
    return report["exit_code"]


def _cmd_generate(args: argparse.Namespace) -> int:
    _apply_runfile(args)
    generate_stack(
        _stack_arg(args),
        config=args.config,
        vars_file=_vars_arg(args),
        input_layers=_ordered_input_layers(args),
        build_dir=args.build_dir,
        no_cache=args.no_cache,
        use_local_modules=args.use_local_modules,
        merge_mode=_merge_mode_arg(args),
    )
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    _apply_runfile(args)
    component_types = args.component_type if args.component_type else None
    results, plan_policies = inspect_modules(
        config=args.config,
        component_types=component_types,
        build_dir=args.build_dir,
        no_cache=args.no_cache,
        merge_mode=_merge_mode_arg(args),
    )

    _emit_info_ci_output(
        InspectOutputFormat(args.format or InspectOutputFormat.TABLE.value),
        json_text_factory=lambda: format_json(results, details=not args.basic),
        table_renderer=lambda: format_table(
            results,
            details=True,
            basic=args.basic,
            plan_policies=plan_policies,
        ),
    )

    return 0


def _cmd_terragrunt_action(args: argparse.Namespace, action: str) -> int:
    _apply_runfile(args)
    return run_stack_action(
        action,
        _stack_arg(args),
        config=args.config,
        vars_file=_vars_arg(args),
        input_layers=_ordered_input_layers(args),
        build_dir=args.build_dir,
        no_cache=args.no_cache,
        auto_approve=args.auto_approve,
        destroy=args.destroy,
        use_local_modules=args.use_local_modules,
        tags=args.tag,
        tag_expr=args.tag_expr,
        save_plan_json=getattr(args, "save_plan_json", None),
        strict_validation_warnings=args.strict_validation_warnings,
        fail_on_changes=getattr(args, "fail_on_changes", False),
        no_cas=getattr(args, "no_cas", False),
        merge_mode=_merge_mode_arg(args),
        validation_report_format=_validation_report_format(args),
    )


def _cmd_operation_run(args: argparse.Namespace) -> int:
    """Run one approved native operation from the selected stack."""
    _apply_runfile(args)
    result = run_stack_operation(
        _stack_arg(args),
        args.operation_name,
        config=args.config,
        vars_file=_vars_arg(args),
        input_layers=_ordered_input_layers(args),
        build_dir=args.build_dir,
        no_cache=args.no_cache,
        merge_mode=_merge_mode_arg(args),
    )
    print(json.dumps(result, sort_keys=True))
    return 0


def _render_environment_preview_table(payload: dict[str, object]) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console(stderr=True)
    console.print()
    console.print("[bold]GitOps Environment Preview[/bold]")
    console.print(
        f"  [cyan]Root:[/cyan] {payload['gitops_root'] or '.'}    "
        f"[cyan]Mode:[/cyan] {payload['discovery_mode']}"
    )
    console.print(f"  [cyan]Common runfile:[/cyan] {payload['common_runfile']}")

    summary = Table(show_header=True, header_style="bold cyan")
    summary.add_column("Metric")
    summary.add_column("Value")
    summary.add_row("Discovered", str(len(payload.get("all_environments", []))))
    summary.add_row("Selected", str(len(payload.get("selected_environments", []))))
    summary.add_row("Changed paths", str(len(payload.get("changed_paths", []))))
    console.print(summary)

    matrix = payload.get("matrix", [])
    matrix_table = Table(show_header=True, header_style="bold cyan")
    matrix_table.add_column("Environment")
    matrix_table.add_column("Runfile")
    for row in matrix:
        matrix_table.add_row(row.get("environment", ""), row.get("runfile", ""))
    console.print(matrix_table)


def _render_ci_validation_table(payload: dict[str, object]) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console(stderr=True)
    console.print()
    console.print("[bold]CI Validation[/bold]")

    summary_values = (
        payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    )
    summary_table = Table(show_header=True, header_style="bold cyan")
    summary_table.add_column("Metric")
    summary_table.add_column("Value")
    summary_table.add_row("Status", str(payload.get("status", "")))
    summary_table.add_row("Exit code", str(payload.get("exit_code", "")))
    summary_table.add_row("Checks passed", str(summary_values.get("pass", "")))
    summary_table.add_row("Checks failed", str(summary_values.get("fail", "")))
    summary_table.add_row("Total checks", str(summary_values.get("total", "")))
    console.print(summary_table)

    results_table = Table(show_header=True, header_style="bold cyan")
    results_table.add_column("Check")
    results_table.add_column("Status")
    results_table.add_column("Message")
    results_table.add_column("Detail")
    for row in payload.get("results", []):
        if isinstance(row, dict):
            name = str(row.get("name", ""))
            status = str(row.get("status", ""))
            message = str(row.get("message", ""))
            detail = row.get("detail")
        else:
            name = ""
            status = ""
            message = str(row)
            detail = None
        detail_text = json.dumps(detail, sort_keys=True) if detail is not None else ""
        results_table.add_row(
            name,
            status,
            message,
            detail_text,
        )
    console.print(results_table)


def _render_cache_diagnostics_table(payload: dict[str, object]) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console(stderr=True)
    console.print()
    console.print("[bold]Stacksmith Diagnostics[/bold]")

    summary = Table(show_header=True, header_style="bold cyan")
    summary.add_column("Field")
    summary.add_column("Value")
    summary.add_row("Stack file", str(payload.get("stack_file", "")))
    summary.add_row(
        "Config paths",
        ", ".join(str(path) for path in payload.get("config_paths", [])),
    )
    summary.add_row("Build directory", str(payload.get("build_directory", "")))
    summary.add_row(
        "Remote cache directory", str(payload.get("remote_cache_directory", ""))
    )
    summary.add_row("Vendor directory", str(payload.get("vendor_directory", "")))
    console.print(summary)

    cache_rows = payload.get("remote_cache_entries", [])
    if payload.get("remote_cache_exists", False):
        cache_table = Table(show_header=True, header_style="bold cyan")
        cache_table.add_column("Remote Cache Entry")
        cache_table.add_column("Type")
        for row in cache_rows:
            cache_table.add_row(str(row.get("name", "")), str(row.get("type", "")))
        console.print(cache_table)
    else:
        console.print("[yellow]Remote cache not found.[/yellow]")

    if payload.get("vendor_directory_exists", False):
        manifest_path = payload.get("vendor_manifest_path")
        if manifest_path:
            console.print(f"[cyan]Vendor manifest:[/cyan] {manifest_path}")

        vendored_modules_table = Table(show_header=True, header_style="bold cyan")
        vendored_modules_table.add_column("Module Key")
        vendored_modules_table.add_column("Source")
        vendored_modules_table.add_column("Version")
        for row in payload.get("vendored_modules", []):
            vendored_modules_table.add_row(
                str(row.get("key", "")),
                str(row.get("source", "")),
                str(row.get("version", "")),
            )
        console.print(vendored_modules_table)

        vendor_dirs = payload.get("vendor_directories", [])
        vendor_dirs_table = Table(show_header=True, header_style="bold cyan")
        vendor_dirs_table.add_column("Vendored module directories")
        for name in vendor_dirs:
            vendor_dirs_table.add_row(str(name))
        console.print(vendor_dirs_table)
    else:
        console.print("[yellow]Vendor directory not found.[/yellow]")


def _emit_info_ci_output(
    output_format: InspectOutputFormat,
    *,
    json_text_factory: Callable[[], str],
    table_renderer: Callable[[], None],
) -> None:
    if output_format == InspectOutputFormat.JSON:
        print(json_text_factory())
        return
    table_renderer()


def _format_info_ci_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, indent=2)


def _cmd_info_environments(args: argparse.Namespace) -> int:
    payload = inspect_environments(
        gitops_root=args.gitops_root,
        discovery_mode=args.discovery_mode,
        environments=args.environments,
        event_name=args.event_name,
        changed_paths=args.changed_path,
        base_ref=args.base_ref,
        before=args.before,
        after=args.after,
    )
    _emit_info_ci_output(
        InspectOutputFormat(args.format),
        json_text_factory=lambda: _format_info_ci_json(payload),
        table_renderer=lambda: _render_environment_preview_table(payload),
    )
    return 0


def _cmd_ci_validate(args: argparse.Namespace) -> int:
    report = validate_ci_inputs(
        gitops_root=args.gitops_root,
        discovery_mode=args.discovery_mode,
        runfile=args.workflow_runfile,
        env_file=args.workflow_env_file,
        validation_report_format=args.workflow_validation_report_format,
    )
    _emit_info_ci_output(
        InspectOutputFormat(args.format),
        json_text_factory=lambda: _format_info_ci_json(report),
        table_renderer=lambda: _render_ci_validation_table(report),
    )
    return report["exit_code"]


def _cmd_diagnose(args: argparse.Namespace) -> int:
    _apply_runfile(args)
    payload = inspect_cache_diagnostics(
        _stack_arg(args),
        config=args.config,
        build_dir=args.build_dir,
        no_cache=args.no_cache,
        merge_mode=_merge_mode_arg(args),
    )
    _emit_info_ci_output(
        InspectOutputFormat(args.format),
        json_text_factory=lambda: _format_info_ci_json(payload),
        table_renderer=lambda: _render_cache_diagnostics_table(payload),
    )
    return 0


def _cmd_run_all(args: argparse.Namespace) -> int:
    _apply_runfile(args)
    if args.action == TerragruntAction.INIT.value and (
        args.tag is not None or args.tag_expr is not None
    ):
        LOGGER.error(
            "--tag and --tag-expr are only supported for run-all plan/apply/destroy"
        )
        return 1
    if args.action != TerragruntAction.PLAN.value and args.save_plan_json is not None:
        LOGGER.error("--save-plan-json is only supported for run-all plan")
        return 1
    if args.action != TerragruntAction.PLAN.value and getattr(
        args, "fail_on_changes", False
    ):
        LOGGER.error("--fail-on-changes is only supported for run-all plan")
        return 1
    if (
        args.action != TerragruntAction.PLAN.value
        and _validation_report_format(args) != ValidationReportFormat.JSON
    ):
        LOGGER.error("--validation-report-format is only supported for run-all plan")
        return 1

    return run_all_stacks(
        args.action,
        args.root,
        config=args.config,
        vars_file=_vars_arg(args),
        input_layers=_ordered_input_layers(args),
        build_dir=args.build_dir,
        no_cache=args.no_cache,
        include_tags=args.include_tag,
        exclude_tags=args.exclude_tag,
        clean=args.clean,
        auto_approve=args.auto_approve,
        destroy=args.destroy,
        use_local_modules=args.use_local_modules,
        tags=args.tag,
        tag_expr=args.tag_expr,
        save_plan_json=args.save_plan_json,
        strict_validation_warnings=args.strict_validation_warnings,
        fail_on_changes=getattr(args, "fail_on_changes", False),
        no_cas=getattr(args, "no_cas", False),
        stacks=_run_all_stack_args(args),
        merge_mode=_merge_mode_arg(args),
        validation_report_format=_validation_report_format(args),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stacksmith", description="YAML/JSON-driven Terragrunt wrapper"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{parser.prog} {metadata_version('stacksmith')}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate
    p_validate = subparsers.add_parser(
        "validate", help="Validate stack schema and variables"
    )
    _add_stack_arg(p_validate)
    _add_common_args(p_validate)
    _add_validation_report_format_arg(p_validate)

    # generate
    p_generate = subparsers.add_parser(
        "generate", help="Generate .tf.json and terragrunt.hcl.json"
    )
    _add_stack_arg(p_generate)
    _add_common_args(p_generate)

    # run-all
    p_run_all = subparsers.add_parser(
        "run-all", help="Discover all stacks and run terragrunt run-all"
    )
    p_run_all.add_argument(
        "action",
        choices=[action.value for action in TerragruntAction],
        help="Terragrunt action to run across all stacks",
    )
    root_default = Path(stacksmith_env("ROOT", str(Path.cwd())))
    p_run_all.add_argument(
        "--root",
        type=_path_type,
        default=root_default,
        required=False,
        help="Root directory to discover stacks in (default: current working directory)",
    )
    _add_stack_arg(p_run_all, include_positional=False)
    _add_common_args(p_run_all)
    _add_validation_report_format_arg(p_run_all)
    _add_plan_output_args(p_run_all)
    _add_target_selection_args(
        p_run_all,
        tag_help=(
            "Select components by tag. Repeat to require multiple tags. "
            "Supported for run-all plan/apply/destroy."
        ),
        tag_expr_help=(
            "JMESPath expression used to select resource targets. "
            "Supported for run-all plan/apply/destroy."
        ),
    )
    p_run_all.add_argument(
        "--include-tag",
        action="append",
        help="Include stacks that have this tag. Repeatable.",
    )
    p_run_all.add_argument(
        "--exclude-tag",
        action="append",
        help="Exclude stacks that have this tag. Repeatable.",
    )
    p_run_all.add_argument(
        "--clean",
        action="store_true",
        help="Remove existing build output directory before generation",
    )
    p_run_all.add_argument(
        "--auto-approve",
        action="store_true",
        help="Skip interactive approval for apply/destroy",
    )

    # init / plan / apply / destroy
    for action in TerragruntAction:
        action_name = action.value
        p_action = subparsers.add_parser(
            action_name,
            help=f"Generate + terragrunt {action_name}",
        )
        _add_stack_arg(p_action)
        _add_common_args(p_action)
        p_action.set_defaults(
            auto_approve=False,
            destroy=False,
            tag=None,
            tag_expr=None,
        )
        match action:
            case TerragruntAction.PLAN:
                _add_plan_output_args(p_action)
                _add_target_selection_args(p_action)
                _add_validation_report_format_arg(p_action)
            case TerragruntAction.APPLY | TerragruntAction.DESTROY:
                _add_target_selection_args(
                    p_action,
                    include_auto_approve=True,
                )

    p_operation = subparsers.add_parser(
        "operation", help="Run native operations approved by managed configuration"
    )
    operation_subparsers = p_operation.add_subparsers(
        dest="operation_command", required=True
    )
    p_operation_run = operation_subparsers.add_parser(
        "run", help="Run one approved operation declared by a stack"
    )
    p_operation_run.add_argument("operation_name", help="Stack-local operation name")
    _add_stack_arg(p_operation_run)
    _add_common_args(p_operation_run)

    # info group
    p_info = subparsers.add_parser(
        "info",
        help="Show stacksmith inspection and diagnostics commands",
    )
    info_subparsers = p_info.add_subparsers(dest="info_command", required=True)

    p_info_inspect = info_subparsers.add_parser(
        "inspect",
        help="Inspect configured modules: variables, mappings, and metadata",
    )
    _configure_inspect_parser(p_info_inspect)

    p_info_diagnose = info_subparsers.add_parser(
        "diagnose",
        help="Show cache and module diagnostics",
    )
    _configure_diagnose_parser(p_info_diagnose)

    p_info_environments = info_subparsers.add_parser(
        "environments",
        help="Preview GitOps environment discovery and selection",
    )
    _configure_info_environments_parser(p_info_environments)

    # ci group
    p_ci = subparsers.add_parser(
        "ci",
        help="CI-focused validation and diagnostics commands",
    )
    ci_subparsers = p_ci.add_subparsers(dest="ci_command", required=True)

    p_ci_validate = ci_subparsers.add_parser(
        "validate",
        help="Validate CI workflow inputs using Stacksmith semantics",
    )
    _configure_ci_validate_parser(p_ci_validate)

    return parser


def main() -> None:
    """CLI entry point."""
    env_files = get_env_file_paths()
    if env_files:
        load_env_files(env_files)

    parser = _build_parser()
    args = parser.parse_args()
    debug_enabled = is_debug_enabled(args)
    quiet_enabled = is_quiet_enabled(args)

    # Parse per-category --log flags, which look like: --log transforms=DEBUG
    def _parse_log_flags(raw: list[str] | None) -> dict[str, int]:
        mapping = {}
        if not raw:
            return mapping
        for entry in raw:
            if "=" not in entry:
                LOGGER.warning(
                    "Ignoring malformed --log entry %r; expected 'category=LEVEL'",
                    entry,
                )
                continue
            name, lvl = entry.split("=", 1)
            name = name.strip()
            lvl_str = lvl.strip().upper()
            if not name:
                LOGGER.warning(
                    "Ignoring malformed --log entry with empty category: %r", entry
                )
                continue
            if lvl_str.isdigit():
                try:
                    levelno = int(lvl_str)
                except ValueError:
                    LOGGER.warning(
                        "Invalid numeric log level %r for category %r; ignoring",
                        lvl_str,
                        name,
                    )
                    continue
            else:
                levelno = logging._nameToLevel.get(lvl_str)
                if levelno is None:
                    LOGGER.warning(
                        "Unknown log level %r for category %r; ignoring", lvl_str, name
                    )
                    continue
            mapping[name] = levelno
        return mapping

    category_levels = _parse_log_flags(getattr(args, "log", None))
    _configure_logging(
        debug=debug_enabled,
        quiet=quiet_enabled,
        category_levels=category_levels,
    )

    try:
        match args.command:
            case "validate":
                exit_code = _cmd_validate(args)
                if exit_code != 0:
                    exit_code = 3  # Validation failure
            case "generate":
                exit_code = _cmd_generate(args)
                if exit_code != 0:
                    exit_code = 6  # Module/configuration error
            case "info":
                match args.info_command:
                    case "inspect":
                        exit_code = _cmd_inspect(args)
                    case "diagnose":
                        exit_code = _cmd_diagnose(args)
                    case "environments":
                        exit_code = _cmd_info_environments(args)
                    case _:
                        parser.print_help(sys.stderr)
                        exit_code = 1
            case "ci":
                match args.ci_command:
                    case "validate":
                        exit_code = _cmd_ci_validate(args)
                    case _:
                        parser.print_help(sys.stderr)
                        exit_code = 1
            case "operation":
                match args.operation_command:
                    case "run":
                        exit_code = _cmd_operation_run(args)
                    case _:
                        parser.print_help(sys.stderr)
                        exit_code = 1
            case command if command in {action.value for action in TerragruntAction}:
                exit_code = _cmd_terragrunt_action(args, command)
                if exit_code != 0:
                    exit_code = 5  # Terragrunt action failed
            case "run-all":
                exit_code = _cmd_run_all(args)
                if exit_code != 0:
                    exit_code = 5  # Terragrunt action failed
            case _:
                parser.print_help(sys.stderr)
                exit_code = 1
    except FileNotFoundError as exc:
        LOGGER.error("{exc}", exc=exc)
        exit_code = 4
    except StacksmithError as exc:
        LOGGER.error("{exc}", exc=exc)
        exit_code = 6
    except (ValueError, RuntimeError) as exc:
        LOGGER.error("{exc}", exc=exc)
        exit_code = 6
    except KeyboardInterrupt:
        LOGGER.warning("Aborted.")
        exit_code = 130

    sys.exit(exit_code)
