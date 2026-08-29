import argparse
import difflib
import json
import sys
from pathlib import Path

from stacksmith.loading.validation import load_fragment_schema

SCHEMA_DIRECTORY = Path("src/stacksmith/schemas")
EFFECTIVE_SCHEMA_NAMES = (
    "config.schema.json",
    "runfile.schema.json",
    "stack.schema.json",
    "test_manifest.schema.json",
    "vars.schema.json",
)


def _layer_schema_name(effective_schema_name: str) -> str:
    return effective_schema_name.replace(".schema.json", ".layer.schema.json")


def render_layer_schema(effective_schema_name: str) -> str:
    """Render one generated layer schema.

    Args:
        effective_schema_name: Filename of the strict effective schema.

    Returns:
        Formatted JSON document
    """
    return json.dumps(load_fragment_schema(effective_schema_name), indent=2)


def update_layer_schemas(check: bool = False) -> int:
    """Update or check all generated editor-facing layer schemas.

    Args:
        check: When `True`, report drift without writing schema files.

    Returns:
        Process exit code.
    """
    has_drift = False
    for effective_schema_name in EFFECTIVE_SCHEMA_NAMES:
        output_path = SCHEMA_DIRECTORY / _layer_schema_name(effective_schema_name)
        rendered = render_layer_schema(effective_schema_name)
        existing = (
            output_path.read_text(encoding="utf-8") if output_path.exists() else ""
        )
        if existing == rendered:
            continue
        has_drift = True
        if check:
            sys.stdout.writelines(
                difflib.unified_diff(
                    existing.splitlines(keepends=True),
                    rendered.splitlines(keepends=True),
                    fromfile=str(output_path),
                    tofile=f"{output_path} (generated)",
                )
            )
        else:
            output_path.write_text(rendered, encoding="utf-8")
    return int(check and has_drift)


def main() -> int:
    """Run the layer-schema generator.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        description="Generate partial layer schemas from strict effective schemas."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report generated schema drift without writing files.",
    )
    return update_layer_schemas(check=parser.parse_args().check)


if __name__ == "__main__":
    sys.exit(main())
