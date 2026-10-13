import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..authoring import StackEdit, author_stack
from ..exceptions import StacksmithConfigError


class AuthoringRequest(BaseModel):
    """Versioned source-authoring input for process-independent clients.

    Attributes:
        version: Contract version, currently 1.
        source: Original authored YAML or JSON text.
        path: Filename selecting the format and labeling the diff.
        edits: Ordered mapping edits to propose.
    """

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    source: str
    path: str
    edits: list[StackEdit] = Field(default_factory=list)


def configure_authoring_parser(parser: argparse.ArgumentParser) -> None:
    """Configure the source-authoring command without runtime configuration flags.

    Args:
        parser: Authoring subcommand parser.

    Returns:
        None.
    """
    parser.add_argument(
        "--request",
        default="-",
        metavar="PATH",
        help="Read a version 1 JSON request from PATH, or stdin when '-' (default).",
    )


def run_authoring(args: argparse.Namespace) -> int:
    """Read a structured request and emit a JSON proposal or JSON error.

    This command performs no file writes, template rendering, environment-file
    loading, managed configuration inspection, or repository operations.

    Args:
        args: Parsed authoring command arguments.

    Returns:
        Zero for a valid proposal, or six for an invalid request or document.
    """
    try:
        request = AuthoringRequest.model_validate_json(
            sys.stdin.read()
            if args.request == "-"
            else Path(args.request).read_text(encoding="utf-8")
        )
    except ValidationError, UnicodeError:
        return _error(
            "invalid_request",
            "Expected a version 1 authoring request with source, path, and edits.",
        )
    except OSError as exc:
        return _error("request_io", str(exc))
    try:
        proposal = author_stack(request.source, request.edits, request.path)
    except StacksmithConfigError as exc:
        return _error("invalid_document", str(exc))
    print(
        json.dumps(
            {"version": 1, "validation": "document", **asdict(proposal)},
            ensure_ascii=False,
        )
    )
    return 0


def _error(code: str, message: str) -> int:
    print(json.dumps({"version": 1, "error": {"code": code, "message": message}}))
    return 6
