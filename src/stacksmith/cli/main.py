import argparse
import contextlib
import json
import logging
import os
import sys
import tempfile
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
    _configure_ci_execute_from_env_parser,
    _configure_ci_execute_parser,
    _configure_ci_prepare_from_env_parser,
    _configure_ci_prepare_parser,
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
from stacksmith.models import FileReference, MergeConfig, MergePolicy
from stacksmith.remote import is_remote_url, resolve_if_remote
from stacksmith.utils import env_truthy, stacksmith_env

from ..api import (
    generate_stack,
    inspect_cache_diagnostics,
    inspect_environments,
    inspect_modules,
    prepare_ci_execution,
    run_all_stacks,
    run_stack_action,
    run_stack_operation,
    validate_ci_inputs,
    validate_stack,
)
from ..enums import InspectOutputFormat, TerragruntAction, ValidationReportFormat
from ..exceptions import StacksmithError
from ..gitops.contracts import CiExecutionManifest
from ..inspector import format_json, format_table
from ..utils import load_env_files
from .args import (
    get_env_file_paths,
    is_debug_enabled,
    is_quiet_enabled,
    parse_input_layers,
)

_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


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
        args.merge_rules = []
        args._runfile_applied = True
        return args

    cli_merge_mode = getattr(args, "merge_mode", None)
    if cli_merge_mode is None and runfile.merge_mode is not None:
        args.merge_mode = runfile.merge_mode.value
    args.merge_rules = [] if cli_merge_mode is not None else runfile.merge_rules

    args.config = [*runfile.configs, *(getattr(args, "config", None) or [])] or None

    run_layers = [("vars", item) for item in runfile.vars]
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


def _merge_mode_arg(args: argparse.Namespace) -> MergeConfig:
    _apply_runfile(args)
    default = MergeMode(getattr(args, "merge_mode", None) or MergeMode.DEEP.value)
    if getattr(args, "merge_rules", None):
        return MergePolicy(default=default, rules=args.merge_rules)
    return default


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
        no_cas=args.no_cas,
        force_rerun=args.force_rerun,
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


def _cmd_ci_prepare(args: argparse.Namespace) -> int:
    manifest = prepare_ci_execution(
        command=args.command,
        operation_name=args.operation_name,
        config_ref=args.config_ref,
        workdir=args.workdir,
        env_file=args.env_file,
        stacksmith_args_json=args.stacksmith_args_json,
        no_cas=args.no_cas,
        force_rerun=args.force_rerun,
        validation_report_format=args.validation_report_format,
        fail_on_changes=args.fail_on_changes,
        strict_validation_warnings=args.strict_validation_warnings,
        gitops_root=args.gitops_root,
        discovery_mode=args.discovery_mode,
        environments=args.environments,
        event_name=args.event_name,
        changed_paths=args.changed_path,
        base_ref=args.base_ref,
        before=args.before,
        after=args.after,
        ref_name=args.ref_name,
        default_branch=args.default_branch,
        is_primary_branch=(
            None if args.is_primary_branch is None else args.is_primary_branch == "true"
        ),
        skip_branch_validation=args.skip_branch_validation,
    )
    _emit_info_ci_output(
        InspectOutputFormat(args.format),
        json_text_factory=lambda: manifest.model_dump_json(indent=2),
        table_renderer=lambda: _render_environment_preview_table(
            {
                "gitops_root": args.gitops_root,
                "discovery_mode": args.discovery_mode,
                "common_runfile": (
                    manifest.matrix[0].runfile if manifest.matrix else ""
                ),
                "all_environments": [row.environment for row in manifest.matrix],
                "selected_environments": [row.environment for row in manifest.matrix],
                "changed_paths": args.changed_path or [],
                "matrix": [row.model_dump() for row in manifest.matrix],
            }
        ),
    )
    return 0


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY_ENV_VALUES


def _optional_env_bool(name: str) -> bool | None:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return None
    return _is_truthy(raw_value)


def _prepare_ci_manifest_from_env() -> CiExecutionManifest:
    return prepare_ci_execution(
        command=os.getenv("INPUT_COMMAND", ""),
        operation_name=os.getenv("INPUT_OPERATION_NAME", ""),
        config_ref=os.getenv("INPUT_CONFIG_REF", ""),
        workdir=os.getenv("INPUT_WORKDIR", "."),
        env_file=os.getenv("INPUT_ENV_FILE", "/dev/null"),
        stacksmith_args_json=os.getenv("INPUT_STACKSMITH_ARGS_JSON", "[]"),
        no_cas=_is_truthy(os.getenv("INPUT_NO_CAS")),
        force_rerun=_is_truthy(os.getenv("INPUT_FORCE_RERUN")),
        validation_report_format=os.getenv("INPUT_VALIDATION_REPORT_FORMAT", "json"),
        fail_on_changes=_is_truthy(os.getenv("INPUT_FAIL_ON_CHANGES")),
        strict_validation_warnings=_is_truthy(
            os.getenv("INPUT_STRICT_VALIDATION_WARNINGS")
        ),
        gitops_root=os.getenv("INPUT_GITOPS_ROOT", "."),
        discovery_mode=os.getenv("INPUT_DISCOVERY_MODE", "auto"),
        environments=os.getenv("INPUT_ENVIRONMENTS", ""),
        event_name=os.getenv("CALLER_EVENT_NAME", ""),
        base_ref=os.getenv("CALLER_BASE_REF", ""),
        before=os.getenv("CALLER_EVENT_BEFORE", ""),
        after=os.getenv("CALLER_SHA", ""),
        ref_name=os.getenv("CALLER_REF_NAME", ""),
        default_branch=os.getenv("CALLER_DEFAULT_BRANCH", ""),
        is_primary_branch=_optional_env_bool("CALLER_IS_PRIMARY_BRANCH"),
        skip_branch_validation=_is_truthy(os.getenv("SKIP_BRANCH_VALIDATION")),
    )


def _manifest_output_json(manifest: CiExecutionManifest, compact: bool = False) -> str:
    if compact:
        return json.dumps(manifest.model_dump(mode="json"), separators=(",", ":"))
    return manifest.model_dump_json(indent=2)


def _write_github_output_manifest(
    manifest: CiExecutionManifest,
    github_output_path: Path,
) -> None:
    matrix = [row.model_dump(mode="json") for row in manifest.matrix]
    with github_output_path.open("a", encoding="utf-8") as output_stream:
        output_stream.write(
            f"manifest={_manifest_output_json(manifest, compact=True)}\n"
        )
        output_stream.write(f"matrix={json.dumps(matrix, separators=(",", ":"))}\n")
        output_stream.write(f"count={len(matrix)}\n")


def _cmd_ci_prepare_from_env(args: argparse.Namespace) -> int:
    manifest = _prepare_ci_manifest_from_env()
    if args.manifest_file is not None:
        args.manifest_file.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_file.write_text(_manifest_output_json(manifest), encoding="utf-8")

    if args.provider == "github-actions":
        output_path = args.github_output or (
            Path(os.environ["GITHUB_OUTPUT"]) if os.getenv("GITHUB_OUTPUT") else None
        )
        if output_path is None:
            raise StacksmithError(
                "GITHUB_OUTPUT is required for provider github-actions"
            )
        _write_github_output_manifest(manifest, output_path)
        return 0

    print(_manifest_output_json(manifest))
    return 0


def _load_ci_execution_manifest(path: Path) -> CiExecutionManifest:
    try:
        return CiExecutionManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StacksmithError(f"Invalid CI execution manifest '{path}': {exc}") from exc


def _ci_execution_argv(manifest: CiExecutionManifest, environment: str) -> list[str]:
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
    if manifest.no_cas:
        common_args.append("--no-cas")
    if manifest.command == "plan":
        return [
            "plan",
            *common_args,
            "--save-plan-json",
            f".stacksmith-ci/{row.environment}/plan.json",
            "--validation-report-format",
            manifest.validation_report_format,
            *(["--fail-on-changes"] if manifest.fail_on_changes else []),
            *(
                ["--strict-validation-warnings"]
                if manifest.strict_validation_warnings
                else []
            ),
        ]
    if manifest.command == "apply":
        return ["apply", *common_args, "--auto-approve"]
    return [
        "operation",
        "run",
        manifest.operation_name,
        *common_args,
        *(["--force-rerun"] if manifest.force_rerun else []),
    ]


def _cmd_ci_execute(args: argparse.Namespace) -> int:
    manifest = _load_ci_execution_manifest(args.manifest)
    return _run_ci_execute(
        manifest,
        args.environment,
        getattr(args, "validation_report_output", None),
    )


def _execute_ci_manifest(manifest: CiExecutionManifest, environment: str) -> int:
    if manifest.env_file != "/dev/null":
        load_env_files([Path(manifest.env_file)])
    original_directory = Path.cwd()
    try:
        os.chdir(manifest.workdir)
        execution_args = _build_parser().parse_args(
            _ci_execution_argv(manifest, environment)
        )
        if manifest.command == "operation":
            return _cmd_operation_run(execution_args)
        return _cmd_terragrunt_action(execution_args, manifest.command)
    finally:
        os.chdir(original_directory)


def _run_ci_execute(
    manifest: CiExecutionManifest,
    environment: str,
    validation_report_output: Path | None,
) -> int:
    if manifest.command != "plan" or validation_report_output is None:
        return _execute_ci_manifest(manifest, environment)

    validation_report_output.parent.mkdir(parents=True, exist_ok=True)
    with validation_report_output.open("w", encoding="utf-8") as output_stream:
        with contextlib.redirect_stdout(output_stream):
            return _execute_ci_manifest(manifest, environment)


def _write_ssh_key_material(environment: str) -> Path | None:
    key_material = os.getenv("STACKSMITH_GIT_SSH_KEY_MATERIAL", "")
    if not key_material.strip():
        return None

    file_descriptor, key_path = tempfile.mkstemp(
        prefix=f"stacksmith_git_ssh_key_{environment}_"
    )
    os.close(file_descriptor)
    path = Path(key_path)
    path.chmod(0o600)
    path.write_text(f"{key_material.rstrip()}\n", encoding="utf-8")
    os.environ["STACKSMITH_GIT_SSH_KEY"] = str(path)
    return path


def _resolve_ci_execution_manifest_path(
    explicit_manifest_file: Path | None,
) -> tuple[Path, Path | None]:
    if explicit_manifest_file is not None:
        return explicit_manifest_file, None

    env_manifest_file = os.getenv("CI_MANIFEST_FILE")
    if env_manifest_file:
        return Path(env_manifest_file), None

    manifest_json = os.getenv("STACKSMITH_CI_MANIFEST", "")
    if not manifest_json.strip():
        raise StacksmithError(
            "Provide --manifest-file, CI_MANIFEST_FILE, or STACKSMITH_CI_MANIFEST"
        )

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        suffix=".json",
    ) as temporary_manifest:
        temporary_manifest.write(manifest_json)

    path = Path(temporary_manifest.name)
    return path, path


def _resolve_ci_environment(explicit_environment: str) -> str:
    environment = (
        explicit_environment.strip()
        or os.getenv("STACKSMITH_ENVIRONMENT", "").strip()
        or os.getenv("ENVIRONMENT", "").strip()
    )
    if not environment:
        raise StacksmithError(
            "Provide --environment, STACKSMITH_ENVIRONMENT, or ENVIRONMENT"
        )
    return environment


def _resolve_validation_report_output(
    args: argparse.Namespace,
    manifest: CiExecutionManifest,
    environment: str,
) -> Path | None:
    if args.validation_report_output is not None:
        return args.validation_report_output

    env_output_path = (
        os.getenv("STACKSMITH_VALIDATION_REPORT_PATH", "").strip()
        or os.getenv("VALIDATION_REPORT_PATH", "").strip()
    )
    if env_output_path:
        return Path(env_output_path)

    if manifest.command != "plan":
        return None

    return (
        Path(manifest.workdir)
        / ".stacksmith-ci"
        / environment
        / f"validation-report.{manifest.validation_report_format}"
    )


def _cmd_ci_execute_from_env(args: argparse.Namespace) -> int:
    manifest_path, temporary_manifest_path = _resolve_ci_execution_manifest_path(
        args.manifest_file
    )
    environment = _resolve_ci_environment(args.environment)
    ssh_key_path = _write_ssh_key_material(environment)
    try:
        manifest = _load_ci_execution_manifest(manifest_path)
        return _run_ci_execute(
            manifest,
            environment,
            _resolve_validation_report_output(args, manifest, environment),
        )
    finally:
        if temporary_manifest_path is not None:
            temporary_manifest_path.unlink(missing_ok=True)
        if ssh_key_path is not None:
            ssh_key_path.unlink(missing_ok=True)


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
    p_operation_run.add_argument(
        "--force-rerun",
        action="store_true",
        default=env_truthy("FORCE_RERUN", prefix="STACKSMITH_"),
        help=(
            "Force the operation runner resource to be replaced even when its "
            "execution identity has not changed. Can also be enabled with "
            "STACKSMITH_FORCE_RERUN=1."
        ),
    )
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

    p_ci_prepare = ci_subparsers.add_parser(
        "prepare",
        help="Validate GitOps policy and emit a provider-neutral execution manifest",
    )
    _configure_ci_prepare_parser(p_ci_prepare)

    p_ci_execute = ci_subparsers.add_parser(
        "execute",
        help="Execute one environment from a manifest emitted by ci prepare",
    )
    _configure_ci_execute_parser(p_ci_execute)

    p_ci_prepare_from_env = ci_subparsers.add_parser(
        "prepare-from-env",
        help="Build a CI manifest from adapter environment variables",
    )
    _configure_ci_prepare_from_env_parser(p_ci_prepare_from_env)

    p_ci_execute_from_env = ci_subparsers.add_parser(
        "execute-from-env",
        help="Execute CI manifest adapter inputs from environment variables",
    )
    _configure_ci_execute_from_env_parser(p_ci_execute_from_env)

    return parser


def main() -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()
    if not (
        args.command == "ci"
        and args.ci_command in {"execute", "execute-from-env", "prepare-from-env"}
    ):
        env_files = get_env_file_paths()
        if env_files:
            load_env_files(env_files)
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
                    case "prepare":
                        exit_code = _cmd_ci_prepare(args)
                    case "execute":
                        exit_code = _cmd_ci_execute(args)
                    case "prepare-from-env":
                        exit_code = _cmd_ci_prepare_from_env(args)
                    case "execute-from-env":
                        exit_code = _cmd_ci_execute_from_env(args)
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
