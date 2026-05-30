import json
import os
from pathlib import Path

from loguru import logger as LOGGER

from .utils import cache_key

#: Default root directory for vendored modules inside the container image.
DEFAULT_VENDOR_DIR = Path("/workspace/.stacksmith/modules")
#: Environment variable to override the vendor directory.
VENDOR_DIR_ENV = "STACKSMITH_VENDOR_DIR"
#: Manifest filename written alongside vendored module directories.
MANIFEST_FILENAME = "vendor-manifest.json"


def get_vendor_dir() -> Path:
    """Return the configured vendored module root directory.

    The environment variable `STACKSMITH_VENDOR_DIR` overrides the default.
    """
    return Path(os.getenv(VENDOR_DIR_ENV, str(DEFAULT_VENDOR_DIR))).expanduser()


def vendor_key(source: str, version: str) -> str:
    """Compute a deterministic directory name for a vendored module.

    The key is a truncated SHA-256 hex digest of the canonical `source|version` string.
    Sixteen hex characters (64 bits) provide sufficient collision resistance for the
    expected number of modules in a single image while keeping paths short.

    Args:
        source: Module source string exactly as it appears in the config.
        version: Module version string exactly as it appears in the config.

    Returns:
        A 16-character lowercase hex string usable as a directory name.
    """
    return cache_key(f"{source}|{version}")


def vendor_path(source: str, version: str, vendor_dir: Path | None = None) -> Path:
    """Return the absolute local path for a vendored module.

    Args:
        source: Module source string from the config.
        version: Module version string from the config.
        vendor_dir: Root directory containing all vendored modules.
            If omitted, the environment variable `STACKSMITH_VENDOR_DIR`
            overrides the embedded default.

    Returns:
        Path to the expected vendored module directory.
    """
    vendor_dir = vendor_dir or get_vendor_dir()
    return vendor_dir / vendor_key(source, version)


def resolve_module_source(
    source: str, version: str, *, vendor_dir: Path | None = None
) -> str:
    """Resolve a module source to a local vendored path if available.

    When the vendored directory for the given *source* and *version* exists,
    this returns a local filesystem path that OpenTofu accepts as a module
    source.  If the directory does not exist, a `FileNotFoundError` is raised
    so the caller can fail fast.

    Args:
        source: Original remote module source from the config.
        version: Module version from the config.
        vendor_dir: Root directory containing all vendored modules.
            If omitted, the environment variable `STACKSMITH_VENDOR_DIR`
            overrides the embedded default.

    Returns:
        Local filesystem path string suitable for the `source` field in
        generated `main.tf.json`.

    Raises:
        FileNotFoundError: If the expected vendored module directory is absent.
    """
    path = vendor_path(source, version, vendor_dir)
    if not path.is_dir():
        raise FileNotFoundError(
            f"Vendored module not found for source={source!r} version={version!r}. "
            f"Expected directory: {path}"
        )
    return str(path)


def write_vendor_manifest(
    modules: dict[str, tuple[str, str]], vendor_dir: Path | None = None
) -> Path:
    """Write a JSON manifest mapping vendor keys back to original sources.

    The manifest enables reverse lookup from a local vendored path to the
    original remote source and version, which is useful for auditing and
    debugging.

    Args:
        modules: Mapping of `{module_type: (source, version)}`.
        vendor_dir: Root directory containing all vendored modules.
            If omitted, the environment variable `STACKSMITH_VENDOR_DIR`
            overrides the embedded default.

    Returns:
        Path to the written manifest file.
    """
    vendor_dir = vendor_dir or get_vendor_dir()
    entries: dict[str, dict[str, str]] = {}
    for module_type, (source, version) in modules.items():
        key = vendor_key(source, version)
        entries[key] = {
            "module_type": module_type,
            "source": source,
            "version": version,
        }
    manifest_path = vendor_dir / MANIFEST_FILENAME
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    LOGGER.debug("Wrote vendor manifest to {path}", path=manifest_path)
    return manifest_path


def load_vendor_manifest(vendor_dir: Path | None = None) -> dict[str, dict[str, str]]:
    """Load and return the vendor manifest.

    Args:
        vendor_dir: Root directory containing all vendored modules.
            If omitted, the environment variable `STACKSMITH_VENDOR_DIR`
            overrides the embedded default.

    Returns:
        Parsed manifest dict keyed by vendor key.

    Raises:
        FileNotFoundError: If the manifest file does not exist.
    """
    vendor_dir = vendor_dir or get_vendor_dir()
    manifest_path = vendor_dir / MANIFEST_FILENAME
    return json.loads(manifest_path.read_text(encoding="utf-8"))
