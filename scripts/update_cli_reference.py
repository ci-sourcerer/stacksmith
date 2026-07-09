import argparse
import difflib
import sys
from pathlib import Path

from stacksmith.cli.main import _build_parser

README_PATH = Path("README.md")
START_MARKER = "<!-- BEGIN GENERATED CLI REFERENCE -->"
END_MARKER = "<!-- END GENERATED CLI REFERENCE -->"


def generate_cli_reference() -> str:
    """Generate the README CLI reference from the argparse parser.

    Returns:
        Markdown content for the generated CLI reference block.
    """
    return "\n".join(
        [
            START_MARKER,
            (
                "Single-stack commands default to `stack.yaml` in the current "
                "directory, with fallback to `stack.yml` then `stack.json`, when "
                "neither `--stack`, `STACKSMITH_STACK`, nor `stacksmith.yaml` "
                "supplies stack refs."
            ),
            "",
            _format_parser_reference(_build_parser(), 3),
            "",
            END_MARKER,
        ]
    )


def update_readme(check: bool = False) -> int:
    """Update or check the README generated CLI reference block.

    Args:
        check: When `True`, report drift without writing the README.

    Returns:
        Process exit code.
    """
    original = README_PATH.read_text(encoding="utf-8")
    updated = replace_generated_block(original, generate_cli_reference())

    if original == updated:
        return 0

    if check:
        sys.stdout.writelines(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=str(README_PATH),
                tofile=f"{README_PATH} (generated)",
            )
        )
        return 1

    README_PATH.write_text(updated, encoding="utf-8")
    return 0


def replace_generated_block(content: str, generated_block: str) -> str:
    """Replace the generated CLI reference block in README content.

    Args:
        content: Existing README content.
        generated_block: Generated markdown block, including markers.

    Returns:
        README content with the generated block replaced.

    Raises:
        ValueError: If the generated block markers are missing or reversed.
    """
    if START_MARKER not in content or END_MARKER not in content:
        return _insert_generated_block(content, generated_block)

    start = content.index(START_MARKER)
    end = content.index(END_MARKER, start) + len(END_MARKER)
    return f"{content[:start]}{generated_block}{content[end:]}"


def main() -> None:
    """Run the CLI reference updater."""
    raise SystemExit(update_readme(check="--check" in sys.argv[1:]))


def _insert_generated_block(content: str, generated_block: str) -> str:
    heading = "## CLI reference\n\n"
    next_heading = "\n### Validation report output"
    if heading not in content:
        raise ValueError("Could not find `## CLI reference` in README.md.")
    if next_heading not in content:
        raise ValueError(
            "Could not find `### Validation report output` after CLI reference."
        )

    start = content.index(heading) + len(heading)
    end = content.index(next_heading, start)
    return f"{content[:start]}{generated_block}\n{content[end:]}"


def _format_parser_reference(
    parser: argparse.ArgumentParser, heading_level: int
) -> str:
    sections = [
        _format_command_reference(parser, heading_level),
        _format_subcommands(parser, heading_level + 1),
    ]
    sections.extend(
        _format_parser_reference(subparser, heading_level)
        for _, subparser in _iter_leaf_parsers(parser)
    )
    return "\n\n".join(section for section in sections if section)


def _format_command_reference(
    parser: argparse.ArgumentParser,
    heading_level: int,
) -> str:
    sections = [
        f"{'#' * heading_level} `{parser.prog}`",
        "",
        "```text",
        _normalize_usage(parser),
        "```",
    ]

    if parser.description:
        sections.extend(["", parser.description])

    if _iter_documented_actions(parser):
        sections.extend(["", _format_action_table(parser)])

    return "\n".join(sections)


def _format_subcommands(
    parser: argparse.ArgumentParser, heading_level: int
) -> str | None:
    subparser_action = _find_subparser_action(parser)
    if subparser_action is None:
        return None

    rows = ["| Command | Description |", "| - | - |"]
    rows.extend(
        f"| `{command}` | {_escape_table_text(help_text)} |"
        for command, help_text in _iter_subcommand_help(subparser_action)
    )
    return "\n".join([f"{'#' * heading_level} Commands", "", *rows])


def _format_action_table(parser: argparse.ArgumentParser) -> str:
    rows = ["| Argument | Description |", "| - | - |"]
    rows.extend(
        f"| `{_format_action_name(action)}` | {_escape_table_text(_format_help(action))} |"
        for action in _iter_documented_actions(parser)
    )
    return "\n".join(rows)


def _normalize_usage(parser: argparse.ArgumentParser) -> str:
    return parser.format_usage().removeprefix("usage: ").strip()


def _iter_leaf_parsers(
    parser: argparse.ArgumentParser,
) -> list[tuple[str, argparse.ArgumentParser]]:
    subparser_action = _find_subparser_action(parser)
    if subparser_action is None:
        return []

    leaf_parsers = []
    for command, subparser in subparser_action.choices.items():
        if _find_subparser_action(subparser) is None:
            leaf_parsers.append((command, subparser))
        else:
            leaf_parsers.extend(_iter_leaf_parsers(subparser))
    return leaf_parsers


def _find_subparser_action(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _iter_subcommand_help(action: argparse._SubParsersAction) -> list[tuple[str, str]]:
    return [
        (choice.dest, choice.help or "")
        for choice in action._choices_actions
        if choice.help is not argparse.SUPPRESS
    ]


def _iter_documented_actions(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [
        action
        for action in parser._actions
        if action.help is not argparse.SUPPRESS
        and not isinstance(action, argparse._HelpAction | argparse._SubParsersAction)
    ]


def _format_action_name(action: argparse.Action) -> str:
    if action.option_strings:
        return ", ".join(action.option_strings)
    return action.metavar or action.dest


def _format_help(action: argparse.Action) -> str:
    help_text = action.help or ""
    if action.choices:
        help_text = f"{_ensure_sentence(help_text)} Choices: {_format_choices(action)}."
    return help_text


def _ensure_sentence(value: str) -> str:
    if not value or value.endswith((".", "!", "?")):
        return value
    return f"{value}."


def _format_choices(action: argparse.Action) -> str:
    return ", ".join(f"`{choice}`" for choice in action.choices)


def _escape_table_text(value: str) -> str:
    return value.replace("\n", " ").replace("|", "\\|")


if __name__ == "__main__":
    main()
