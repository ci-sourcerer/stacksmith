from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .exceptions import StacksmithConfigError
from .models import (
    FileReference,
    ModuleSourceReference,
    ProviderSourceReference,
    render_file_reference,
    render_module_source_fields,
    render_provider_source_fields,
)

FileReferenceFormatter = Callable[
    [FileReference | str | Path, Mapping[str, Any] | None],
    str,
]
ModuleSourceFormatter = Callable[
    [ModuleSourceReference, Mapping[str, Any] | None],
    dict[str, str],
]
ProviderSourceFormatter = Callable[
    [ProviderSourceReference, Mapping[str, Any] | None],
    dict[str, str],
]


def _render_file_reference_default(
    reference: FileReference | str | Path, _options: Mapping[str, Any] | None = None
) -> str:
    return render_file_reference(reference)


def _render_module_source_terraform(
    source: ModuleSourceReference, options: Mapping[str, Any] | None = None
) -> dict[str, str]:
    return render_module_source_fields(source, options=options)


def _render_provider_source_terraform(
    source: ProviderSourceReference, options: Mapping[str, Any] | None = None
) -> dict[str, str]:
    return render_provider_source_fields(source, options=options)


FILE_REFERENCE_FORMATTERS: dict[str, FileReferenceFormatter] = {
    "cli": _render_file_reference_default,
    "terraform": _render_file_reference_default,
    "terragrunt": _render_file_reference_default,
}

MODULE_SOURCE_FORMATTERS: dict[str, ModuleSourceFormatter] = {
    "terraform": _render_module_source_terraform,
    "terragrunt": _render_module_source_terraform,
}

PROVIDER_SOURCE_FORMATTERS: dict[str, ProviderSourceFormatter] = {
    "terraform": _render_provider_source_terraform,
    "terragrunt": _render_provider_source_terraform,
}


def render_file_reference_for(
    target: str,
    reference: FileReference | str | Path,
    *,
    options: Mapping[str, Any] | None = None,
) -> str:
    """Render a file reference string for a target formatter.

    Args:
        target: Formatter target name.
        reference: File reference to render.
        options: Optional renderer options.

    Returns:
        Rendered string for the target.

    Raises:
        StacksmithConfigError: If the target is not supported.
    """
    formatter = FILE_REFERENCE_FORMATTERS.get(target)
    if formatter is None:
        raise StacksmithConfigError(
            f"Unknown file reference formatter target '{target}'. "
            f"Available targets: {', '.join(sorted(FILE_REFERENCE_FORMATTERS))}"
        )
    return formatter(reference, options)


def render_module_source_for(
    target: str,
    source: ModuleSourceReference,
    *,
    options: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Render module source fields for a target formatter.

    Args:
        target: Formatter target name.
        source: Structured module source reference.
        options: Optional renderer options.

    Returns:
        Mapping of module source fields expected by the target.

    Raises:
        StacksmithConfigError: If the target is not supported.
    """
    formatter = MODULE_SOURCE_FORMATTERS.get(target)
    if formatter is None:
        raise StacksmithConfigError(
            f"Unknown module source formatter target '{target}'. "
            f"Available targets: {', '.join(sorted(MODULE_SOURCE_FORMATTERS))}"
        )
    return formatter(source, options)


def render_provider_source_for(
    target: str,
    source: ProviderSourceReference,
    *,
    options: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Render provider source fields for a target formatter.

    Args:
        target: Formatter target name.
        source: Structured provider source reference.
        options: Optional renderer options.

    Returns:
        Mapping of provider source fields expected by the target.

    Raises:
        StacksmithConfigError: If the target is not supported.
    """
    formatter = PROVIDER_SOURCE_FORMATTERS.get(target)
    if formatter is None:
        raise StacksmithConfigError(
            f"Unknown provider source formatter target '{target}'. "
            f"Available targets: {', '.join(sorted(PROVIDER_SOURCE_FORMATTERS))}"
        )
    return formatter(source, options)
