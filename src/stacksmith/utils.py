import hashlib
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from loguru import logger as LOGGER

from .exceptions import StacksmithConfigError


def _load_dotenv_values(path: Path) -> dict[str, str | None]:
    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {path}")
    return dotenv_values(path)


def normalize_path_input(path: Path | Sequence[Path], empty_error: str) -> list[Path]:
    """Normalize one or many paths into a non-empty list.

    Args:
        path: Single path or ordered sequence of paths.
        empty_error: Error message used when no paths are provided.

    Returns:
        Normalized list of paths.

    Raises:
        StacksmithConfigError: If no paths are provided.
    """
    paths = [path] if isinstance(path, Path) else list(path)
    if not paths:
        raise StacksmithConfigError(empty_error)
    return paths


def load_env_file(path: Path) -> None:
    """Load environment variables from a file.

    Args:
        path: Path to the environment file.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    for key, value in _load_dotenv_values(path).items():
        if value is None:
            continue
        if key not in os.environ:
            os.environ[key] = value


def load_env_files(paths: Sequence[Path]) -> None:
    """Load environment variables from multiple files in order.

    When the same key appears in multiple env files, later files override earlier
    env file values.

    Args:
        paths: Ordered env file paths to load.

    Raises:
        FileNotFoundError: If an env file does not exist.
    """
    for path in paths:
        for key, value in _load_dotenv_values(path).items():
            if value is None:
                continue
            os.environ[key] = value


def parse_bool(value: str | None, default: bool = False) -> bool:
    """Parse a truthy string value.

    Args:
        value: Raw string value.
        default: Value returned for `None` or an empty string.

    Returns:
        `True` for `1`, `true`, `yes`, or `on`, ignoring case; otherwise `False`.
    """
    if value is None or not value.strip():
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_truthy(name: str, default: bool = False, prefix: str | None = None) -> bool:
    """Return `True` for truthy environment variables.

    Args:
        name: Name of the environment variable to check.
        default: Default boolean value to return if the variable is not set or empty.
        prefix: Optional prefix to prepend to the variable name if not already present.

    Returns:
        `True` if the environment variable is set to a truthy value, `False` otherwise.
    """
    env_name = name
    if prefix is not None and not name.startswith(prefix):
        env_name = f"{prefix}{name}"
    return parse_bool(os.getenv(env_name), default=default)


def stacksmith_env(
    name: str, default: str | None = None, prefix: str = "STACKSMITH_"
) -> str | None:
    """Return a Stacksmith-prefixed environment variable.

    Args:
        name: Name of the setting without the prefix or with the prefix already.
        default: Default value to return when the variable is unset.
        prefix: Prefix to apply when resolving the variable name.

    Returns:
        The resolved environment value, or `default` if unset.
    """
    return os.getenv(name if name.startswith(prefix) else f"{prefix}{name}", default)


def stacksmith_env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    prefix: str = "STACKSMITH_",
) -> int:
    """Return a validated integer from a Stacksmith environment variable.

    Args:
        name: Name of the setting without the prefix or with the prefix already.
        default: Value returned when the environment variable is unset.
        minimum: Optional inclusive minimum accepted value.
        prefix: Prefix to apply when resolving the environment variable name.

    Returns:
        The parsed integer or `default` when the variable is unset.

    Raises:
        StacksmithConfigError: If the configured value is not a valid integer or is
            less than `minimum`.
    """
    variable_name = name if name.startswith(prefix) else f"{prefix}{name}"
    raw_value = os.getenv(variable_name)
    try:
        value = default if raw_value is None else int(raw_value)
    except ValueError as exc:
        raise StacksmithConfigError(
            f"{variable_name} must be an integer, got '{raw_value}'"
        ) from exc
    if minimum is not None and value < minimum:
        raise StacksmithConfigError(
            f"{variable_name} must be at least {minimum}, got {value}"
        )
    return value


def stacksmith_env_list(
    name: str, default: list[str] | None = None, prefix: str = "STACKSMITH_"
) -> list[str] | None:
    """Return a Stacksmith-prefixed environment variable as a list.

    Supports colon-delimited values. Items containing colons, such as remote
    URLs, must be quoted.
    """
    raw_value = stacksmith_env(name, prefix=prefix)
    if raw_value is None:
        return default
    stripped = raw_value.strip()
    if not stripped:
        return default

    if "://" in stripped and stripped.count(":") == 1:
        return [stripped]

    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False

    for char in stripped:
        if escaped:
            current.append(char)
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if quote is not None:
            if char == quote:
                quote = None
            else:
                current.append(char)
            continue

        if char in {'"', "'"}:
            quote = char
            continue

        if char == ":":
            item = "".join(current).strip()
            if item:
                items.append(item)
            current = []
            continue

        current.append(char)

    if escaped:
        current.append("\\")
    if quote is not None:
        raise StacksmithConfigError(
            f"Environment variable {name!r} has an unterminated quote"
        )
    item = "".join(current).strip()
    if item:
        items.append(item)
    return items or default


def cache_key(value: str) -> str:
    """Return a short, deterministic cache key for a string.

    Args:
        value: String to hash.

    Returns:
        First 16 hexadecimal characters of the string's SHA-256 digest.
    """
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def derive_stack_state_key(
    stack_name: str, source_path: Path | None, root: Path | None = None
) -> str:
    """Return the backend state key for a stack.

    Args:
        stack_name: Logical stack name.
        source_path: Path to the stack definition file.
        root: Optional monorepo root used for relative state key derivation.

    Returns:
        State key path ending in `terraform.tfstate`.
    """
    if root is not None and source_path is not None:
        rel = source_path.parent.relative_to(root.resolve())
        return str(rel).replace("\\", "/") + "/terraform.tfstate"
    return f"{stack_name}/terraform.tfstate"


def get_current_git_repository(path: Path | None = None) -> str | None:
    """Return the `origin` URL for the Git repository containing a path.

    Args:
        path: Directory within the repository to inspect. Uses the current working
            directory when omitted.

    Returns:
        The configured `origin` remote URL, or `None` when the target directory
        is not in a Git repository, has no `origin` remote, or Git is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=path or Path.cwd(),
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None

    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def clone_git_repo(
    repo_url: str, dest: Path, ref: str | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Shallow-clone a git repository into a destination directory.

    Args:
        repo_url: Repository URL to clone.
        dest: Directory where the clone should be written.
        ref: Optional branch or tag to check out.
        env: Optional environment overrides for the git process.

    Returns:
        The completed git subprocess result.

    Raises:
        RuntimeError: If git is unavailable on PATH.
    """
    if shutil.which("git") is None:
        raise RuntimeError(
            "git is not installed or not on PATH. Install git to clone remote repos."
        )

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    cmd = [
        "git",
        "clone",
        "--depth",
        "1",
        *(["--branch", ref] if ref else []),
        repo_url,
        str(dest),
    ]
    LOGGER.debug("Cloning git repo: {cmd}", cmd=" ".join(cmd))
    return subprocess.run(cmd, env=env, capture_output=True, text=True, check=True)


def resolve_git_env(host: str, auth_config: dict[str, Any] | None) -> dict[str, str]:
    """Build Git environment overrides for SSH or token authentication.

    Args:
        host: Git host whose authentication settings should be resolved.
        auth_config: Optional authentication settings keyed by host.

    Returns:
        Current process environment with matching Git authentication overrides.
    """
    env = os.environ.copy()

    ssh_key: str | None = None
    if auth_config:
        entry = auth_config.get(host)
        if entry is not None and entry.type == "ssh" and entry.ssh_key_path:
            ssh_key = entry.ssh_key_path

    if not ssh_key:
        ssh_key = stacksmith_env("GIT_SSH_KEY")

    if ssh_key:
        env["GIT_SSH_COMMAND"] = f"ssh -i {ssh_key} -o StrictHostKeyChecking=accept-new"

    token: str | None = None
    if auth_config:
        entry = auth_config.get(host)
        if entry is not None and entry.type == "token" and entry.token_env:
            token = os.getenv(entry.token_env)

    if not token:
        token = stacksmith_env("GIT_TOKEN")

    if token:
        env["GIT_ASKPASS"] = "echo"
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = (
            f"url.https://x-access-token:{token}@{host}/.insteadOf"
        )
        env["GIT_CONFIG_VALUE_0"] = f"https://{host}/"

    return env


def env_vars(prefix: str = "STACKSMITH_") -> dict[str, str]:
    """Return all current environment variables with the given Stacksmith prefix.

    Args:
        prefix: Prefix to filter environment variables by.

    Returns:
        Dict of environment variable names to values, including only variables that
        start with the prefix.
    """
    return {key: value for key, value in os.environ.items() if key.startswith(prefix)}
