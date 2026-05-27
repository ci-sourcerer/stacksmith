import argparse
import logging
import sys
from importlib.metadata import version as metadata_version
from pathlib import Path

from loguru import logger as LOGGER
from stacksmith.cli.args import (
    _add_common_args,
    _add_stack_arg,
    _configure_diagnose_parser,
    _configure_inspect_parser,
    _path_type,
)
from stacksmith.utils import stacksmith_env

from ..api import (
    diagnose_cache,
    generate_stack,
    inspect_modules,
    run_all_stacks,
    run_stack_action,
    validate_stack,
)
from ..exceptions import StacksmithError
from ..inspector import format_json, format_table, format_yaml
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
) -> list[tuple[str, str]] | None:
    return parse_input_layers(getattr(args, "input_layers", None))


def _vars_arg(args: argparse.Namespace) -> list[str] | None:
    raw_layers = getattr(args, "input_layers", None)
    if raw_layers and any(kind == "vars" for kind, _ in raw_layers):
        return []
    return getattr(args, "vars_file", None)


def _cmd_validate(args: argparse.Namespace) -> int:
    return validate_stack(
        args.stack_file,
        config=args.config,
        vars_file=_vars_arg(args),
        input_layers=_ordered_input_layers(args),
        build_dir=args.build_dir,
        no_cache=args.no_cache,
        strict_validation_warnings=args.strict_validation_warnings,
    )


def _cmd_generate(args: argparse.Namespace) -> int:
    return generate_stack(
        args.stack_file,
        config=args.config,
        vars_file=_vars_arg(args),
        input_layers=_ordered_input_layers(args),
        build_dir=args.build_dir,
        no_cache=args.no_cache,
        use_local_modules=args.use_local_modules,
    )


def _cmd_inspect(args: argparse.Namespace) -> int:
    resource_types = args.resource_type if args.resource_type else None
    results, plan_policies = inspect_modules(
        config=args.config,
        resource_types=resource_types,
        build_dir=args.build_dir,
        no_cache=args.no_cache,
    )

    match args.format or "table":
        case "json":
            print(format_json(results, details=not args.basic))
        case "yaml":
            print(format_yaml(results, details=not args.basic))
        case "table" | _:
            format_table(
                results,
                details=True,
                basic=args.basic,
                plan_policies=plan_policies,
            )

    return 0


def _cmd_terragrunt_action(args: argparse.Namespace, action: str) -> int:
    return run_stack_action(
        action,
        args.stack_file,
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
    )


def _cmd_diagnose(args: argparse.Namespace) -> int:
    return diagnose_cache(
        args.stack_file,
        config=args.config,
        build_dir=args.build_dir,
        no_cache=args.no_cache,
    )


def _cmd_run_all(args: argparse.Namespace) -> int:
    if args.action == "init" and (args.tag is not None or args.tag_expr is not None):
        LOGGER.error(
            "--tag and --tag-expr are only supported for run-all plan/apply/destroy"
        )
        return 1
    if args.action != "plan" and args.save_plan_json is not None:
        LOGGER.error("--save-plan-json is only supported for run-all plan")
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
        choices=["init", "plan", "apply", "destroy"],
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
    _add_common_args(p_run_all)
    p_run_all.add_argument(
        "--destroy",
        action="store_true",
        help="Plan destroy operations instead of a create/update when action is plan.",
    )
    p_run_all.add_argument(
        "--save-plan-json",
        type=_path_type,
        default=None,
        help="Save rendered plan JSON for each stack to the given directory when action is plan.",
    )
    p_run_all.add_argument(
        "--tag",
        action="append",
        default=None,
        help=(
            "Select resources by tag. Repeat to require multiple tags. "
            "Supported for run-all plan/apply/destroy."
        ),
    )
    p_run_all.add_argument(
        "--tag-expr",
        type=str,
        default=None,
        help=(
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
    for action in ("init", "plan", "apply", "destroy"):
        p_action = subparsers.add_parser(action, help=f"Generate + terragrunt {action}")
        _add_stack_arg(p_action)
        _add_common_args(p_action)
        p_action.set_defaults(
            auto_approve=False,
            destroy=False,
            tag=None,
            tag_expr=None,
        )
        match action:
            case "plan":
                p_action.add_argument(
                    "--destroy",
                    action="store_true",
                    default=False,
                    help="Plan a destroy operation instead of a regular plan.",
                )
                p_action.add_argument(
                    "--save-plan-json",
                    type=_path_type,
                    default=None,
                    help="Save rendered plan JSON to the given file or directory.",
                )
                p_action.add_argument(
                    "--tag",
                    action="append",
                    default=None,
                    help="Select resources by tag. Repeat to require multiple tags.",
                )
                p_action.add_argument(
                    "--tag-expr",
                    type=str,
                    default=None,
                    help="JMESPath expression used to select resource targets.",
                )
            case "apply" | "destroy":
                p_action.add_argument(
                    "--auto-approve",
                    action="store_true",
                    default=False,
                    help="Skip interactive approval",
                )
                p_action.add_argument(
                    "--tag",
                    action="append",
                    default=None,
                    help="Select resources by tag. Repeat to require multiple tags.",
                )
                p_action.add_argument(
                    "--tag-expr",
                    type=str,
                    default=None,
                    help="JMESPath expression used to select resource targets.",
                )

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

    return parser


def main() -> None:
    """CLI entry point."""
    if env_files := get_env_file_paths():
        load_env_files(env_files)

    parser = _build_parser()
    args = parser.parse_args()
    debug_enabled = is_debug_enabled(args)
    quiet_enabled = is_quiet_enabled(args)

    # Parse per-category --log flags, which look like: --log transforms=DEBUG
    def _parse_log_flags(raw: list[str] | None) -> dict[str, int]:
        mapping: dict[str, int] = {}
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
                    case _:
                        parser.print_help(sys.stderr)
                        exit_code = 1
            case "init" | "plan" | "apply" | "destroy":
                exit_code = _cmd_terragrunt_action(args, args.command)
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
        LOGGER.error("Error: {exc}", exc=exc)
        exit_code = 4
    except StacksmithError as exc:
        LOGGER.error("Error: {exc}", exc=exc)
        exit_code = 6
    except (ValueError, RuntimeError) as exc:
        LOGGER.error("Error: {exc}", exc=exc)
        exit_code = 6
    except KeyboardInterrupt:
        LOGGER.warning("Aborted.")
        exit_code = 130

    sys.exit(exit_code)
