import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import hcl2
from loguru import logger as LOGGER

from .exceptions import StacksmithConfigError
from .utils import cache_key as _cache_key
from .utils import clone_git_repo as _clone_git_repo
from .utils import resolve_git_env as _resolve_git_env
from .vendor import vendor_path

if TYPE_CHECKING:
    from .models import RemoteAuthConfig


def _find_module_subdir_separator(source: str) -> int:
    scheme_index = source.find("://")
    return source.find("//", scheme_index + 3 if scheme_index != -1 else 0)


def _split_module_source(source: str) -> tuple[str, Path]:
    normalized = source.strip()
    for prefix in ("git::", "git+"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break

    separator_index = _find_module_subdir_separator(normalized)
    if separator_index == -1:
        return normalized, Path(".")

    repo_url = normalized[:separator_index]
    module_path = (
        normalized[separator_index + 2 :]
        .split("?", 1)[0]
        .split("#", 1)[0]
        .split("@", 1)[0]
    )
    return repo_url, Path(module_path or ".")


def resolve_module_dir(
    source: str,
    version: str,
    *,
    cache_dir: Path | None = None,
    auth_config: "RemoteAuthConfig | None" = None,
    vendor_dir: Path | None = None,
) -> Path:
    """Resolve a module source to a local directory for introspection.

    Checks vendored modules first, then falls back to a shallow git clone.

    Args:
        source: Module source URL (Git-backed).
        version: Module version / git tag.
        cache_dir: Cache directory for git clones.
        auth_config: Optional host-keyed auth configuration.
        vendor_dir: Vendored module root directory.

    Returns:
        Local directory containing the module's OpenTofu files.

    Raises:
        StacksmithConfigError: If the source cannot be resolved for introspection.
        FileNotFoundError: If the resolved source directory or expected module path is
            not found.
    """
    repo_url, module_path = _split_module_source(source)

    if version == "local":
        local_dir = Path(source).expanduser()
        if not local_dir.is_absolute():
            local_dir = (Path.cwd() / local_dir).resolve()
        if local_dir.is_dir():
            LOGGER.debug(
                "Using local module directory for introspection: {path}", path=local_dir
            )
            return local_dir
        raise FileNotFoundError(f"Local module directory not found: {local_dir}")

    if vendor_dir is not None:
        vp = vendor_path(source, version, vendor_dir)
        if vp.is_dir():
            LOGGER.debug("Using vendored module for introspection: {path}", path=vp)
            return vp

    if cache_dir is None:
        raise StacksmithConfigError(
            f"Cannot introspect module {source}@{version} without a cache directory"
        )

    clone_dir = (
        cache_dir / "introspect" / f"{_cache_key(repo_url)}-{_cache_key(version)}"
    )
    module_dir = clone_dir / module_path
    if clone_dir.is_dir():
        if not module_dir.is_dir():
            raise FileNotFoundError(
                f"Module subdirectory '{module_path}' not found in {clone_dir}"
            )

        if not any(module_dir.glob("*.tf")) and not any(module_dir.glob("*.tf.json")):
            LOGGER.debug(
                "Cached clone appears incomplete, refreshing introspection cache: {path}",
                path=clone_dir,
            )
            shutil.rmtree(clone_dir)
            _clone_module(repo_url, version, clone_dir, auth_config)
            if not module_dir.is_dir():
                raise FileNotFoundError(
                    f"Module subdirectory '{module_path}' not found in {clone_dir}"
                )

        LOGGER.debug("Introspection cache hit: {path}", path=module_dir)
        return module_dir

    _clone_module(repo_url, version, clone_dir, auth_config)
    if not module_dir.is_dir():
        raise FileNotFoundError(
            f"Module subdirectory '{module_path}' not found in cloned repo {repo_url}"
        )
    return module_dir


def _clone_module(
    source: str, version: str, dest: Path, auth_config: "RemoteAuthConfig | None"
) -> None:
    host = urlparse(source).hostname or ""
    LOGGER.debug(
        "Cloning module source for introspection: {source} (host: {host})",
        source=source,
        host=host,
    )
    env = _resolve_git_env(host, auth_config)

    result = _clone_git_repo(source, dest, ref=version, env=env)
    if result.returncode != 0:
        raise RuntimeError(
            f"Git clone failed for introspection of {source}@{version} "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )


def discover_module_variables(
    source: str,
    version: str,
    *,
    cache_dir: Path | None = None,
    auth_config: "RemoteAuthConfig | None" = None,
    vendor_dir: Path | None = None,
) -> set[str]:
    """Discover variable names declared by a OpenTofu module.

    Resolves the module source to a local directory (via vendor or git clone), parses
    all `.tf` and `.tf.json` files with `python-hcl2`, and returns the set of top-level
    `variable` block names.

    Args:
        source: Module source URL.
        version: Module version string.
        cache_dir: Cache directory for cloning remote modules.
        auth_config: Optional host-keyed auth configuration.
        vendor_dir: Vendored module root directory.

    Returns:
        Set of variable names the module declares.
    """
    return parse_module_variables(
        resolve_module_dir(
            source,
            version,
            cache_dir=cache_dir,
            auth_config=auth_config,
            vendor_dir=vendor_dir,
        )
    )


def parse_module_variables(module_dir: Path) -> set[str]:
    """Parse `.tf` and `.tf.json` files in a directory and return declared variable names.

    Args:
        module_dir: Directory containing OpenTofu files.

    Returns:
        Set of variable names found in `variable` blocks.
    """
    variables = set()
    variables |= _parse_hcl_variables(module_dir)
    variables |= _parse_json_variables(module_dir)

    if not variables:
        LOGGER.debug(
            "No variables found in {path} for introspection",
            path=module_dir,
        )
    else:
        LOGGER.debug(
            "Discovered {count} variables in {path}: {vars}",
            count=len(variables),
            path=module_dir,
            vars=sorted(variables),
        )
    return variables


def _parse_hcl_variables(module_dir: Path) -> set[str]:
    variables = set()
    for tf_file in sorted(module_dir.glob("*.tf")):
        try:
            with open(tf_file, encoding="utf-8") as f:
                parsed = hcl2.load(f)
        except Exception as exc:
            LOGGER.warning(
                "Failed to parse {file} during introspection: {exc}",
                file=tf_file,
                exc=exc,
            )
            continue

        for var_block in parsed.get("variable", []):
            if isinstance(var_block, dict):
                for key in var_block:
                    variables.add(key.strip('"'))
    return variables


def _parse_json_variables(module_dir: Path) -> set[str]:
    variables = set()
    for tf_json_file in sorted(module_dir.glob("*.tf.json")):
        try:
            data = json.loads(tf_json_file.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning(
                "Failed to parse {file} during introspection: {exc}",
                file=tf_json_file,
                exc=exc,
            )
            continue

        var_section = data.get("variable", {})
        match var_section:
            case dict():
                variables |= set(var_section.keys())
            case list():
                for var_block in var_section:
                    if isinstance(var_block, dict):
                        variables |= set(var_block.keys())
    return variables
