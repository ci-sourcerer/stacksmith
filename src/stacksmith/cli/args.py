import argparse
import os
from pathlib import Path

from ..enums import InspectOutputFormat, MergeMode, ValidationReportFormat
from ..exceptions import StacksmithConfigError
from ..utils import env_truthy, stacksmith_env, stacksmith_env_list

STACKSMITH_LOG_CATEGORIES = (
    "stacksmith.api",
    "stacksmith.cli.args",
    "stacksmith.cli.main",
    "stacksmith.generator",
    "stacksmith.inspector",
    "stacksmith.introspection",
    "stacksmith.remote",
    "stacksmith.runner",
    "stacksmith.terragrunt",
    "stacksmith.utils",
    "stacksmith.validation",
    "stacksmith.vendor",
)

_STACKSMITH_LOG_CATEGORIES_HELP = ", ".join(STACKSMITH_LOG_CATEGORIES)


class _OrderedInputAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str,
        option_string=None,
    ) -> None:
        current_values = list(getattr(namespace, self.dest) or [])
        current_values.append(values)
        setattr(namespace, self.dest, current_values)

        kind = "vars" if option_string == "--vars" else "var"
        current_layers = list(getattr(namespace, "input_layers", None) or [])
        current_layers.append((kind, values))
        setattr(namespace, "input_layers", current_layers)


def is_debug_enabled(args: argparse.Namespace | None = None) -> bool:
    """Check if debug mode is enabled.

    Args:
        args: Command-line arguments namespace.

    Returns:
        `True` if debug mode is enabled, `False` otherwise.
    """
    if args is not None and getattr(args, "debug", False):
        return True
    return env_truthy("DEBUG", prefix="STACKSMITH_")


def is_quiet_enabled(args: argparse.Namespace | None = None) -> bool:
    """Check if quiet mode is enabled.

    Args:
        args: Command-line arguments namespace.

    Returns:
        `True` if quiet mode is enabled, `False` otherwise.
    """
    return bool(args is not None and getattr(args, "quiet", False))


def parse_var_args(var_list: list[str] | None) -> dict[str, str]:
    """Parse a list of key=value strings into a dictionary.

    Args:
        var_list: List of strings in the format key=value.

    Returns:
        Dictionary of parsed key-value pairs.

    Raises:
        StacksmithConfigError: If an entry is not in `key=value` format.
    """
    if not var_list:
        return {}
    result = {}
    for item in var_list:
        key, val = _parse_var_assignment(item)
        result[key] = val
    return result


def _parse_var_assignment(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise StacksmithConfigError(
            f"Invalid --var format: {value}. Expected key=value."
        )
    key, val = value.split("=", 1)
    return key.strip(), val.strip()


def parse_input_layers(
    input_layers: list[tuple[str, object]] | None,
) -> list[tuple[str, object]] | None:
    """Validate and normalize ordered CLI input layers.

    Args:
        input_layers: Ordered `(kind, value)` entries collected during parsing.

    Returns:
        The normalized ordered input layers, or `None` when none were provided.

    Raises:
        StacksmithConfigError: If a `var` layer is not in `key=value` format.
    """
    if not input_layers:
        return None

    normalized_layers: list[tuple[str, object]] = []
    for kind, value in input_layers:
        if kind == "var":
            if not isinstance(value, str):
                raise StacksmithConfigError(
                    "Invalid --var value in input layer; expected key=value string."
                )
            _parse_var_assignment(value)
        normalized_layers.append((kind, value))
    return normalized_layers


def _path_type(value: str) -> Path:
    return Path(value).expanduser()


def get_env_file_paths(argv: list[str] | None = None) -> list[Path] | None:
    """Determine the .env file paths from command-line arguments.

    Args:
        argv: List of command-line arguments. If `None`, defaults to sys.argv.

    Returns:
        Ordered list of .env file paths if specified, otherwise `None`.
    """
    parser = argparse.ArgumentParser(add_help=False)
    _add_env_file_arg(parser)
    args, _ = parser.parse_known_args(argv)
    if args.env_file:
        return args.env_file

    default_path = Path.cwd() / ".env"
    if default_path.exists():
        return [default_path]
    return None


def get_env_file_path(argv: list[str] | None = None) -> Path | None:
    """Return the last `--env-file` path when callers only expect one."""
    paths = get_env_file_paths(argv)
    if not paths:
        return None
    return paths[-1]


def get_default_run_file() -> str | None:
    """Return the default runfile reference from env or local auto-detection."""
    runfile = stacksmith_env("RUN_FILE")
    if runfile:
        return runfile

    default_path = Path.cwd() / "stacksmith.yaml"
    if default_path.exists():
        return str(default_path)
    return None


def get_default_stack_refs() -> list[str]:
    """Return default stack references from env or local auto-detection."""
    stack_refs = stacksmith_env_list("STACK")
    if stack_refs:
        return stack_refs
    return [str(Path.cwd() / "stack.yaml")]


def _add_logging_verbosity_args(parser: argparse.ArgumentParser) -> None:
    verbosity_group = parser.add_mutually_exclusive_group()
    verbosity_group.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help=("Enable debug logging. " "Can also be enabled via STACKSMITH_DEBUG=1."),
    )
    verbosity_group.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress non-error stacksmith logs while still streaming Terragrunt output.",
    )


def _add_env_file_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env-file",
        type=_path_type,
        action="append",
        default=None,
        help=(
            "Load environment variables from a .env file before resolving config and variables. "
            "Repeat to layer multiple env files; later files override earlier env-file values, "
            "while pre-existing environment variables are preserved."
        ),
    )


def _add_plan_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--destroy",
        action="store_true",
        default=False,
        help="Plan destroy operations instead of a create/update when action is plan.",
    )
    parser.add_argument(
        "--save-plan-json",
        type=_path_type,
        default=None,
        help="Save rendered plan JSON to the given file or directory.",
    )
    parser.add_argument(
        "--fail-on-changes",
        action="store_true",
        default=False,
        help="Return a non-zero exit code if the plan contains any resource changes.",
    )


def _add_target_selection_args(
    parser: argparse.ArgumentParser,
    *,
    include_auto_approve: bool = False,
    tag_help: str | None = None,
    tag_expr_help: str | None = None,
) -> None:
    parser.add_argument(
        "--tag",
        action="append",
        default=None,
        help=(tag_help or "Select components by tag. Repeat to require multiple tags."),
    )
    parser.add_argument(
        "--tag-expr",
        default=None,
        help=(tag_expr_help or "JMESPath expression used to select resource targets."),
    )
    if include_auto_approve:
        parser.add_argument(
            "--auto-approve",
            action="store_true",
            default=False,
            help="Skip interactive approval",
        )


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--runfile",
        action="append",
        default=None,
        help=(
            "Path or URL to stacksmith.yaml. Repeat to layer multiple runfiles; "
            "later files override earlier scalar values, dicts merge recursively, "
            "and lists append. When omitted, STACKSMITH_RUN_FILE is used if set, "
            "otherwise ./stacksmith.yaml is auto-detected when present."
        ),
    )
    parser.add_argument(
        "-c",
        "--config",
        action="append",
        default=None,
        required=False,
        help=(
            "Path or URL to stacksmith-config.yaml. Repeat to layer multiple configs; "
            "later files override earlier scalar values, dicts merge recursively, "
            "and lists append. Supports http(s):// and git+ URLs. "
            "If omitted, STACKSMITH_CONFIG can "
            f"provide one or more paths separated by '{os.pathsep}'."
        ),
    )
    _add_env_file_arg(parser)
    parser.add_argument(
        "--vars",
        dest="vars_file",
        action=_OrderedInputAction,
        default=None,
        help=(
            "Path or URL to vars YAML/JSON file. Repeat to layer multiple vars files; "
            "later files override earlier scalar values, dicts merge recursively, and lists append. "
            "Supports http(s):// and git+ URLs."
        ),
    )
    parser.add_argument(
        "--var",
        action=_OrderedInputAction,
        dest="vars",
        help="Variable override in key=value format (repeatable)",
    )
    parser.add_argument(
        "--merge-mode",
        choices=[mode.value for mode in MergeMode],
        default=None,
        help=(
            "Merge strategy for layered stacks, configs, and vars. "
            "Use 'deep' (default) for recursive merging or 'override' so later layers replace earlier ones."
        ),
    )
    parser.add_argument(
        "--build-dir",
        type=_path_type,
        default=None,
        help="Build output directory (default: .stacksmith/ alongside stack file)",
    )
    parser.add_argument(
        "--log",
        action="append",
        default=None,
        help=(
            "Set per-category logging levels in the form 'category=LEVEL'. "
            "Repeatable. LEVEL is one of DEBUG, INFO, WARNING, ERROR, CRITICAL. "
            f"CATEGORY is typically one of {_STACKSMITH_LOG_CATEGORIES_HELP}, "
            "or any Python logger name (for example, urllib3)."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        help="Force re-fetch of remote resources, ignoring the local cache.",
    )
    parser.add_argument(
        "--strict-validation-warnings",
        action="store_true",
        default=False,
        help=(
            "Treat warning outcomes from plan validations as failures. "
            "This only affects plan and run-all plan commands."
        ),
    )
    _use_local_default = env_truthy("ONLY_USE_LOCAL_MODULES", prefix="STACKSMITH_")
    local_modules_group = parser.add_mutually_exclusive_group()
    local_modules_group.add_argument(
        "--use-local-modules",
        action="store_true",
        default=_use_local_default,
        dest="use_local_modules",
        help=(
            "Rewrite module sources to local vendored paths instead of remote URLs. "
            "Can also be enabled via STACKSMITH_ONLY_USE_LOCAL_MODULES=1. "
        ),
    )
    local_modules_group.add_argument(
        "--no-local-modules",
        action="store_false",
        dest="use_local_modules",
        help="Disable local module rewriting even if STACKSMITH_ONLY_USE_LOCAL_MODULES is set.",
    )
    _add_logging_verbosity_args(parser)


def _add_validation_report_format_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--validation-report-format",
        choices=[format_name.value for format_name in ValidationReportFormat],
        default=ValidationReportFormat.JSON.value,
        help=(
            "Format for machine-readable validation reports emitted by "
            "validate, plan, and run-all plan."
        ),
    )


def _configure_inspect_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "component_type",
        nargs="*",
        help="Component type(s) to inspect. Inspects all when omitted.",
    )
    parser.add_argument(
        "--format",
        choices=[format_name.value for format_name in InspectOutputFormat],
        default=None,
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--basic",
        action="store_true",
        default=False,
        help="Show only input, validation, and transform columns in the module table.",
    )
    _add_common_args(parser)


def _configure_diagnose_parser(parser: argparse.ArgumentParser) -> None:
    _add_stack_arg(parser)
    _add_common_args(parser)


def _add_stack_arg(
    parser: argparse.ArgumentParser,
    *,
    include_positional: bool = True,
) -> None:
    parser.add_argument(
        "--stack",
        action="append",
        default=None,
        help=(
            "Path or URL to a stack definition file. Repeat to deep-merge multiple "
            "stack layers for single-stack commands, or to target explicit stacks for run-all."
        ),
    )
    if include_positional:
        parser.add_argument(
            "stack_file",
            type=_path_type,
            nargs="?",
            default=(
                Path(stacksmith_env("STACK"))
                if stacksmith_env("STACK") is not None
                else None
            ),
            help=(
                "Optional path to stack.yaml, stack.yml, or stack.json. When omitted, "
                "stacksmith falls back to --stack, STACKSMITH_STACK, or ./stack.yaml."
            ),
        )
