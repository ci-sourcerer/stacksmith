import json
from pathlib import Path
from typing import Any

from loguru import logger as LOGGER

from .models import StackDefinition, ToolConfig


def generate_terragrunt_json(
    stack: StackDefinition,
    config: ToolConfig,
    resolved_vars: dict[str, Any],
    dependency_stacks: dict[str, StackDefinition] | None = None,
    dependency_build_dirs: dict[str, Path] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Generate the Terragrunt configuration as a Python dict (for terragrunt.hcl.json).

    Args:
        stack: Parsed stack definition.
        config: Tool configuration.
        resolved_vars: Resolved variable values to include in inputs.
        dependency_stacks: Map of dependency name to `StackDefinition` (for `mock_outputs`).
        dependency_build_dirs: Map of dependency name to build output directory (for `config_path`).
        root: Monorepo root directory. When provided, the state key is derived from the
            stack file's path relative to this root. When absent, falls back to
            `{name}/terraform.tfstate`.

    Returns:
        Dict representing the full terragrunt.hcl.json structure.
    """
    dependency_stacks = dependency_stacks or {}
    dependency_build_dirs = dependency_build_dirs or {}

    backend_type = config.backend.type
    if root is not None and stack.source_path is not None:
        rel = stack.source_path.parent.relative_to(root.resolve())
        state_key = str(rel).replace("\\", "/") + "/terraform.tfstate"
    else:
        state_key = f"{stack.name}/terraform.tfstate"

    remote_state = {
        "backend": backend_type,
        "config": config.backend.config_with_state_key(state_key),
    }

    doc: dict[str, Any] = {
        "terraform": {"source": "."},
        "remote_state": remote_state,
        "terraform_binary": "tofu",
    }

    if stack.depends_on:
        deps: dict[str, Any] = {}
        for dep_name in stack.depends_on:
            dep_stack = dependency_stacks.get(dep_name)
            dep_build_dir = dependency_build_dirs.get(dep_name)

            config_path = f"../{dep_name}"
            if dep_build_dir:
                config_path = str(dep_build_dir)

            dep_block: dict[str, Any] = {"config_path": config_path}
            if dep_stack and dep_stack.mock_outputs:
                dep_block["mock_outputs"] = dep_stack.mock_outputs
                dep_block["mock_outputs_allowed_terraform_commands"] = [
                    "plan",
                    "validate",
                ]

            deps[dep_name] = dep_block
        doc["dependency"] = deps

    doc["inputs"] = dict(resolved_vars)

    return doc


def write_terragrunt_json(
    stack: StackDefinition,
    config: ToolConfig,
    resolved_vars: dict[str, Any],
    output_dir: Path,
    dependency_stacks: dict[str, StackDefinition] | None = None,
    dependency_build_dirs: dict[str, Path] | None = None,
    root: Path | None = None,
) -> Path:
    """Generate and write terragrunt.hcl.json to the output directory.

    Args:
        stack: Parsed stack definition.
        config: Tool configuration.
        resolved_vars: Resolved variable values.
        output_dir: Directory to write terragrunt.hcl.json into.
        dependency_stacks: Map of dependency name to `StackDefinition`.
        dependency_build_dirs: Map of dependency name to build output directory.
        root: Monorepo root directory used for state key derivation.

    Returns:
        Path to the written terragrunt.hcl.json file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    doc = generate_terragrunt_json(
        stack, config, resolved_vars, dependency_stacks, dependency_build_dirs, root
    )
    output_path = output_dir / "terragrunt.hcl.json"
    output_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    LOGGER.debug("Wrote generated Terragrunt JSON: {path}", path=output_path)
    return output_path
