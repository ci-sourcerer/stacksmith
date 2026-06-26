import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import requests
from loguru import logger as LOGGER
from rich.console import Console
from rich.prompt import Prompt

from .exceptions import StacksmithNotFoundError, StacksmithRemoteError
from .models import is_file_reference_remote, render_file_reference
from .utils import cache_key as _cache_key
from .utils import (
    clone_git_repo,
    env_truthy,
    parse_git_plus_reference,
)
from .utils import resolve_git_env as _resolve_git_env
from .utils import (
    stacksmith_env,
)

if TYPE_CHECKING:
    from .models import FileReference, RemoteAuthConfig

_REMOTE_PREFIXES = ("http://", "https://", "git+https://", "git+ssh://")


@dataclass(frozen=True)
class GitRef:
    """Parsed components of a `git+` remote reference."""

    repo_url: str
    path: str
    ref: str | None


def is_remote_url(reference: str | Path | "FileReference") -> bool:
    """Return `True` when `reference` is a remote URL or structured remote ref."""
    if is_file_reference_remote(reference):
        return True
    normalized = render_file_reference(reference)
    return any(normalized.startswith(prefix) for prefix in _REMOTE_PREFIXES)


def parse_git_url(url: str) -> GitRef:
    """Parse a `git+` URL into its components.

    Expected format::

        git+https://github.com/org/repo.git//path/within/repo@ref
        git+ssh://git@github.com/org/repo.git//path/within/repo@ref

    Args:
        url: Full `git+` URL string.

    Returns:
        Parsed GitRef with repo_url, path, and optional ref.

    Raises:
        StacksmithRemoteError: If the URL does not match the expected format.
    """
    try:
        repo_url, path, ref = parse_git_plus_reference(url)
    except ValueError as exc:
        raise StacksmithRemoteError(str(exc)) from exc
    return GitRef(repo_url=repo_url, path=path, ref=ref)


def resolve_reference_path(
    reference: str | Path | "FileReference",
    *,
    base_path: Path | None,
    cache_dir: Path | None = None,
    auth_config: "RemoteAuthConfig | None" = None,
    missing_cache_error_factory: Callable[[str], Exception] | None = None,
    relative_path_error_factory: Callable[[str], Exception] | None = None,
    not_found_error_factory: Callable[[Path], Exception] | None = None,
) -> Path:
    """Resolve a local/remote reference into an existing local filesystem path.

    Args:
        reference: Local path, structured file reference, or remote URL.
        base_path: Base directory used for resolving relative local paths.
        cache_dir: Cache directory required for remote references.
        auth_config: Optional host-keyed auth configuration for remote fetching.
        missing_cache_error_factory: Optional callback used to raise a custom
            exception when `cache_dir` is required but not provided.
        relative_path_error_factory: Optional callback used to raise a custom
            exception when resolving a relative local path requires `base_path`.
        not_found_error_factory: Optional callback used to raise a custom
            exception when the resolved path does not exist.

    Returns:
        Resolved local path.

    Raises:
        StacksmithRemoteError: If a remote reference cannot be resolved due to
            missing cache configuration.
        StacksmithNotFoundError: If the resolved local path does not exist.
    """
    rendered = render_file_reference(reference)
    if is_remote_url(reference):
        if cache_dir is None:
            if missing_cache_error_factory is not None:
                raise missing_cache_error_factory(rendered)
            raise StacksmithRemoteError(
                "Cannot fetch remote reference without a cache directory: "
                f"{rendered}"
            )
        return resolve_remote(reference, cache_dir, auth_config)

    local_path = Path(rendered)
    if not local_path.is_absolute():
        if base_path is None:
            if relative_path_error_factory is not None:
                raise relative_path_error_factory(rendered)
            raise StacksmithRemoteError(
                f"Cannot resolve relative local path without a base path: {rendered}"
            )
        local_path = base_path / local_path

    resolved_path = local_path.resolve()
    if not resolved_path.exists():
        if not_found_error_factory is not None:
            raise not_found_error_factory(resolved_path)
        raise StacksmithNotFoundError(f"Reference not found: {resolved_path}")

    return resolved_path


def _resolve_auth_headers(
    host: str,
    auth_config: RemoteAuthConfig | None,
) -> dict[str, str]:
    if auth_config:
        entry = auth_config.get(host)
        if entry is not None:
            return entry.to_http_headers()

    token = stacksmith_env("HTTP_TOKEN")
    if token:
        return {"Authorization": f"Bearer {token}"}

    username = stacksmith_env("HTTP_USERNAME")
    password = stacksmith_env("HTTP_PASSWORD")
    if username and password:
        return {"Authorization": requests.auth._basic_auth_str(username, password)}

    return {}


def _has_http_credentials(host: str, auth_config: RemoteAuthConfig | None) -> bool:
    if auth_config and auth_config.get(host) is not None:
        return True
    if stacksmith_env("HTTP_TOKEN"):
        return True
    if stacksmith_env("HTTP_USERNAME") and stacksmith_env("HTTP_PASSWORD"):
        return True
    return False


def _has_git_credentials(host: str, auth_config: RemoteAuthConfig | None) -> bool:
    if auth_config and auth_config.get(host) is not None:
        return True
    if stacksmith_env("GIT_TOKEN"):
        return True
    if stacksmith_env("GIT_SSH_KEY"):
        return True
    return False


def _interactive_http_auth(host: str) -> None:
    console = Console(stderr=True)
    console.print(
        f"[bold]No HTTP auth configured for {host}. Enter credentials if required.[/bold]"
    )
    choice = Prompt.ask(
        "Authentication method",
        choices=["token", "basic", "none"],
        default="none",
        console=console,
    )
    match choice:
        case "token":
            token = Prompt.ask("HTTP token", password=True, console=console)
            os.environ["STACKSMITH_HTTP_TOKEN"] = token
        case "basic":
            username = Prompt.ask("HTTP username", console=console)
            password = Prompt.ask("HTTP password", password=True, console=console)
            os.environ["STACKSMITH_HTTP_USERNAME"] = username
            os.environ["STACKSMITH_HTTP_PASSWORD"] = password


def _interactive_git_auth(host: str) -> None:
    console = Console(stderr=True)
    console.print(
        f"[bold]No Git auth configured for {host}. Enter credentials if required.[/bold]"
    )
    choice = Prompt.ask(
        "Authentication method",
        choices=["ssh", "token", "none"],
        default="ssh",
        console=console,
    )
    match choice:
        case "ssh":
            ssh_key = Prompt.ask("SSH key path", console=console)
            os.environ["STACKSMITH_GIT_SSH_KEY"] = ssh_key
        case "token":
            token = Prompt.ask("Git token", password=True, console=console)
            os.environ["STACKSMITH_GIT_TOKEN"] = token


def _prompt_for_remote_auth(
    reference: str, auth_config: RemoteAuthConfig | None
) -> None:
    if not sys.stdin.isatty():
        return
    if reference.startswith("git+"):
        parsed = parse_git_url(reference)
        host = urlparse(parsed.repo_url).hostname or ""
        if not _has_git_credentials(host, auth_config):
            _interactive_git_auth(host)
        return
    parsed = urlparse(reference)
    host = parsed.hostname or ""
    if not _has_http_credentials(host, auth_config):
        _interactive_http_auth(host)


def _fetch_http(
    url: str,
    cache_dir: Path,
    auth_config: RemoteAuthConfig | None,
) -> Path:
    parsed = urlparse(url)
    filename = Path(parsed.path).name or "downloaded"
    dest_dir = cache_dir / "http" / _cache_key(url)
    dest = dest_dir / filename

    if dest.exists():
        LOGGER.debug("HTTP cache hit: {dest}", dest=dest)
        return dest

    dest_dir.mkdir(parents=True, exist_ok=True)
    headers = _resolve_auth_headers(parsed.hostname or "", auth_config)
    verify = env_truthy("SSL_VERIFY", default=True, prefix="STACKSMITH_")
    LOGGER.debug("Fetching HTTP resource: {url}", url=url)
    resp = requests.get(url, headers=headers, verify=verify, timeout=60)
    if resp.status_code in {401, 403}:
        _prompt_for_remote_auth(url, auth_config)
        headers = _resolve_auth_headers(parsed.hostname or "", auth_config)
        resp = requests.get(url, headers=headers, verify=verify, timeout=60)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    LOGGER.debug("Cached HTTP resource to: {dest}", dest=dest)
    return dest


def _fetch_git(
    parsed: GitRef,
    cache_dir: Path,
    auth_config: RemoteAuthConfig | None,
) -> Path:
    ref_label = parsed.ref or "HEAD"
    clone_dir = (
        cache_dir / "git" / f"{_cache_key(parsed.repo_url)}-{_cache_key(ref_label)}"
    )
    target = clone_dir / parsed.path

    if target.exists():
        LOGGER.debug("Git cache hit: {target}", target=target)
        return target

    host = urlparse(parsed.repo_url).hostname or ""
    env = _resolve_git_env(host, auth_config)

    result = clone_git_repo(
        parsed.repo_url,
        clone_dir,
        ref=parsed.ref,
        env=env,
    )
    if result.returncode != 0:
        if not _has_git_credentials(host, auth_config) and sys.stdin.isatty():
            _prompt_for_remote_auth(
                f"git+{parsed.repo_url}//{parsed.path}@{parsed.ref or ''}",
                auth_config,
            )
            env = _resolve_git_env(host, auth_config)
            result = clone_git_repo(
                parsed.repo_url,
                clone_dir,
                ref=parsed.ref,
                env=env,
            )
        if result.returncode != 0:
            raise StacksmithRemoteError(
                f"Git clone failed (exit {result.returncode}): {result.stderr.strip()}"
            )

    if not target.exists():
        raise StacksmithNotFoundError(
            f"Path '{parsed.path}' not found in cloned repo {parsed.repo_url}"
        )

    LOGGER.debug("Cloned git resource to: {target}", target=target)
    return target


def resolve_remote(
    reference: str | Path | "FileReference",
    cache_dir: Path,
    auth_config: RemoteAuthConfig | None = None,
) -> Path:
    """Fetch a remote resource and return a local cached path.

    Args:
        reference: HTTP(S) URL or `git+` URL string.
        cache_dir: Root cache directory (e.g. `.stacksmith/.cache`).
        auth_config: Optional host-keyed auth configuration from the tool config.

    Returns:
        Local `Path` to the fetched (and cached) resource.

    Raises:
        StacksmithRemoteError: If the URL scheme is not recognised.
        requests.HTTPError: If the HTTP request fails.
        StacksmithRemoteError: If a git clone fails.
        StacksmithNotFoundError: If the requested path does not exist in the cloned repo.
    """
    normalized = render_file_reference(reference)
    if not is_remote_url(reference):
        raise StacksmithRemoteError(f"Not a remote URL: {normalized}")

    if normalized.startswith("git+"):
        _require_git()
        return _fetch_git(parse_git_url(normalized), cache_dir, auth_config)

    return _fetch_http(normalized, cache_dir, auth_config)


def read_reference_content(
    reference: str | Path | "FileReference",
    cache_dir: Path,
    auth_config: RemoteAuthConfig | None = None,
) -> str:
    path = resolve_if_remote(reference, cache_dir, auth_config=auth_config)
    if not path.exists():
        raise StacksmithNotFoundError(f"Reference not found: {reference}")
    if path.is_dir():
        raise IsADirectoryError(f"Reference is a directory: {reference}")
    return path.read_text(encoding="utf-8")


def _require_git() -> None:
    if shutil.which("git") is None:
        raise StacksmithRemoteError(
            "git is not installed or not on PATH. "
            "Install git to use git+ remote URLs."
        )


def resolve_if_remote(
    reference: str | Path | "FileReference",
    cache_dir: Path,
    auth_config: RemoteAuthConfig | None = None,
) -> Path:
    """Return a local `Path` — fetching first when the reference is a remote URL.

    For local paths the string is returned as-is wrapped in a `Path`. For
    remote URLs the resource is fetched (or served from cache) and the cached
    path is returned.

    Args:
        reference: Local file path or remote URL string.
        cache_dir: Root cache directory.
        auth_config: Optional host-keyed auth configuration.

    Returns:
        Local `Path` to the resource.
    """
    if is_remote_url(reference):
        return resolve_remote(reference, cache_dir, auth_config)
    return Path(render_file_reference(reference))
