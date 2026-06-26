import hashlib
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from loguru import logger as LOGGER

from .exceptions import StacksmithConfigError

_GIT_PLUS_URL_RE = re.compile(
    r"^git\+(?P<repo_url>https?://[^/]+/[^@]+?|ssh://[^/]+/[^@]+?)"
    r"//(?P<path>[^@]+)"
    r"(?:@(?P<ref>.+))?$"
)


def _load_dotenv_values(path: Path) -> dict[str, str | None]:
    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {path}")
    return dotenv_values(path)


def parse_git_plus_reference(value: str) -> tuple[str, str, str | None]:
    """Parse a git+ reference into repository URL, path, and optional ref.

    Args:
        value: Git+ reference in the form `git+<proto>://<host>/<repo>//path[@ref]`.

    Returns:
        Tuple containing `(repo_url, path, ref)`.

    Raises:
        ValueError: If the input does not match the expected git+ shape.
    """
    match = _GIT_PLUS_URL_RE.match(value)
    if match is None:
        raise ValueError(
            "Invalid git URL format: "
            f"{value}. Expected: git+<proto>://<host>/<repo>//path[@ref]"
        )
    return match.group("repo_url"), match.group("path"), match.group("ref")


def render_jinja_template_values(
    value: Any,
    context: Mapping[str, Any],
    *,
    jinja_env: Any,
) -> Any:
    """Render Jinja templates recursively in dict/list structures.

    Args:
        value: Value to render.
        context: Rendering context available to Jinja templates.
        jinja_env: Jinja environment used to render template strings.

    Returns:
        Rendered value with the same nested structure as the input.
    """
    if isinstance(value, str) and "{{" in value:
        return jinja_env.from_string(value).render(context)
    if isinstance(value, dict):
        return {
            key: render_jinja_template_values(nested, context, jinja_env=jinja_env)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [
            render_jinja_template_values(item, context, jinja_env=jinja_env)
            for item in value
        ]
    return value


def normalize_path_input(
    path: Path | Sequence[Path],
    *,
    empty_error: str,
) -> list[Path]:
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
    value = os.getenv(env_name, "")
    if not value:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


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
    """Return a short, deterministic cache key for a string."""
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


def clone_git_repo(
    repo_url: str,
    dest: Path,
    *,
    ref: str | None = None,
    env: dict[str, str] | None = None,
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
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


def resolve_git_env(
    host: str,
    auth_config: dict[str, Any] | None,
) -> dict[str, str]:
    """Build git environment overrides for SSH or token auth."""
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


def print_to_stderr(message: str) -> None:
    """Print a message to standard error.

    Args:
        message: The message to print.
    """
    print(message, file=sys.stderr)
