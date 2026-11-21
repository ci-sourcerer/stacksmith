import hashlib
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

_GENERATED_DIRECTORIES = frozenset(
    {".git", ".stacksmith", ".terraform", ".terragrunt-cache", "__pycache__"}
)


def _package_digest(package: Path) -> str:
    digest = hashlib.sha256()
    pending = [(package, frozenset())]
    while pending:
        entry, ancestors = pending.pop()
        if entry.is_dir():
            resolved = entry.resolve()
            if entry.name in _GENERATED_DIRECTORIES or resolved in ancestors:
                continue
            pending.extend(
                (child, ancestors | {resolved})
                for child in sorted(entry.iterdir(), reverse=True)
            )
        elif entry.is_file():
            digest.update(entry.relative_to(package).as_posix().encode())
            digest.update(b"\0")
            digest.update((entry.stat().st_mode & 0o111).to_bytes(1))
            with entry.open("rb") as source:
                digest.update(hashlib.file_digest(source, "sha256").digest())
    return digest.hexdigest()


def version_local_module_sources(modules: MutableMapping[str, Any]) -> None:
    """Add content-based cache identities to generated local module sources.

    Args:
        modules: Generated OpenTofu module blocks to update in place.
    """
    fingerprints: dict[Path, str] = {}
    for module in modules.values():
        if not isinstance(source := module.get("source"), str):
            continue
        package_path, separator, subdirectory = source.partition("//")
        package = Path(package_path)
        if not package.is_absolute() or not package.is_dir():
            continue
        package = package.resolve()
        if package not in fingerprints:
            fingerprints[package] = _package_digest(package)
        module["source"] = (
            f"{package.as_uri()}{separator}{quote(subdirectory)}"
            f"?stacksmith_digest={fingerprints[package]}"
        )
