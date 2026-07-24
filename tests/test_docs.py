import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "update_cli_reference",
    Path("scripts/update_cli_reference.py"),
)
assert _SPEC is not None
_UPDATE_CLI_REFERENCE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_UPDATE_CLI_REFERENCE)


def test_cli_reference_contains_nested_commands():
    reference = _UPDATE_CLI_REFERENCE.generate_cli_reference()

    assert "### `stacksmith plan`" in reference
    assert "### `stacksmith info inspect`" in reference
    assert "### `stacksmith ci validate`" in reference
    assert "| `--validation-report-format` |" in reference


def test_readme_cli_reference_is_current():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert (
        _UPDATE_CLI_REFERENCE.replace_generated_block(
            readme,
            _UPDATE_CLI_REFERENCE.generate_cli_reference(),
        )
        == readme
    )


def test_replace_generated_block_requires_cli_heading_when_markers_are_missing():
    with pytest.raises(ValueError, match="CLI reference"):
        _UPDATE_CLI_REFERENCE.replace_generated_block(
            "# Stacksmith\n",
            _UPDATE_CLI_REFERENCE.generate_cli_reference(),
        )


def test_replace_generated_block_replaces_existing_markers():
    assert (
        _UPDATE_CLI_REFERENCE.replace_generated_block(
            "before\n"
            f"{_UPDATE_CLI_REFERENCE.START_MARKER}\n"
            "stale\n"
            f"{_UPDATE_CLI_REFERENCE.END_MARKER}\n"
            "after\n",
            f"{_UPDATE_CLI_REFERENCE.START_MARKER}\n"
            "fresh\n"
            f"{_UPDATE_CLI_REFERENCE.END_MARKER}",
        )
        == "before\n"
        f"{_UPDATE_CLI_REFERENCE.START_MARKER}\n"
        "fresh\n"
        f"{_UPDATE_CLI_REFERENCE.END_MARKER}\n"
        "after\n"
    )
