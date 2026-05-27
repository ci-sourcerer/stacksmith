import argparse
import os
import sys
from pathlib import Path

from loguru import logger as LOGGER

from ..utils import env_truthy, stacksmith_env

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
        option_string: str | None = None,
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
        True if debug mode is enabled, False otherwise.
    """
    if args is not None and getattr(args, "debug", False):
        return True
    return env_truthy("DEBUG", prefix="STACKSMITH_")


def is_quiet_enabled(args: argparse.Namespace | None = None) -> bool:
    """Check if quiet mode is enabled.

    Args:
        args: Command-line arguments namespace.

    Returns:
        True if quiet mode is enabled, False otherwise.
    """
    return bool(args is not None and getattr(args, "quiet", False))


def parse_var_args(var_list: list[str] | None) -> dict[str, str]:
    """Parse a list of key=value strings into a dictionary.

    Args:
        var_list: List of strings in the format key=value.

    Returns:
        Dictionary of parsed key-value pairs.
    """
    if not var_list:
        return {}
    result: dict[str, str] = {}
    for item in var_list:
        if "=" not in item:
            LOGGER.error("Invalid --var format: {item}. Expected key=value.", item=item)
            sys.exit(1)
        key, val = item.split("=", 1)
        result[key.strip()] = val.strip()
    return result


def parse_input_layers(
    input_layers: list[tuple[str, str]] | None,
) -> list[tuple[str, str]] | None:
    """Validate and normalize ordered CLI input layers.

    Args:
        input_layers: Ordered `(kind, value)` entries collected during parsing.

    Returns:
        The normalized ordered input layers, or `None` when none were provided.
    """
    if not input_layers:
        return None

    normalized_layers: list[tuple[str, str]] = []
    for kind, value in input_layers:
        if kind == "var" and "=" not in value:
            LOGGER.error(
                "Invalid --var format: {item}. Expected key=value.", item=value
            )
            sys.exit(1)
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
    parser.add_argument("--env-file", type=_path_type, action="append", default=None)
    args, _ = parser.parse_known_args(argv)
    if args.env_file:
        return args.env_file

    default_path = Path.cwd() / ".env"
    if default_path.exists():
        return [default_path]
    return None


def get_env_file_path(argv: list[str] | None = None) -> Path | None:
    """Return the last `--env-file` path when callers only expect one."""
    if not (paths := get_env_file_paths(argv)):
        return None
    return paths[-1]


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


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--config",
        type=str,
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
        type=str,
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


def _configure_inspect_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "resource_type",
        nargs="*",
        help="Resource type(s) to inspect. Inspects all when omitted.",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json", "yaml"],
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


def _add_stack_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "stack_file",
        type=_path_type,
        nargs="?",
        default=Path(stacksmith_env("STACK", str(Path.cwd() / "stack.yaml"))),
        help=(
            "Path to stack.yaml, stack.yml, or stack.json."
            " Defaults to stack.yaml in the current directory and falls back"
            " to stack.yml or stack.json if stack.yaml is missing."
            " Can also be overridden by STACKSMITH_STACK."
        ),
    )
