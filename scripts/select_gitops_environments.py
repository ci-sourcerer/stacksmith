#!/usr/bin/env python3
"""Select GitOps environments for workflow fan-out.

This script intentionally avoids importing the `stacksmith` package directly. It
loads `src/stacksmith/gitops.py` by file path so it can run in plain GitHub
Actions `python3` steps without project dependencies installed.
"""

import json
import os
import pathlib
import sys
from importlib.util import module_from_spec, spec_from_file_location
from types import ModuleType


def _load_module(module_path: pathlib.Path, repo_root: pathlib.Path) -> ModuleType:
    module_path = repo_root / module_path
    spec = spec_from_file_location("stacksmith_gitops", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load GitOps helper module at {module_path}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_outputs(matrix: list[dict[str, str]]) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        output_file = pathlib.Path(output_path)
        with output_file.open("a", encoding="utf-8") as handle:
            handle.write(f"matrix={json.dumps(matrix)}\n")
            handle.write(f"count={len(matrix)}\n")
        return

    print(f"matrix={json.dumps(matrix)}")
    print(f"count={len(matrix)}")


def _main() -> int:
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    gitops = _load_module("src/stacksmith/gitops.py", repo_root)

    selection = gitops.evaluate_environment_selection(
        gitops_root=os.getenv("INPUT_GITOPS_ROOT", ""),
        discovery_mode=os.getenv("INPUT_DISCOVERY_MODE", "folders"),
        manual_environments=os.getenv("INPUT_ENVIRONMENTS", ""),
        event_name=os.getenv("CALLER_EVENT_NAME", ""),
        changed_paths=None,
        base_ref=os.getenv("CALLER_BASE_REF", ""),
        before=os.getenv("CALLER_EVENT_BEFORE", ""),
        after=os.getenv("CALLER_SHA", ""),
    )

    _write_outputs(selection.matrix)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
