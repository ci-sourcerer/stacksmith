from graphlib import CycleError, TopologicalSorter
from pathlib import Path

from .exceptions import StacksmithConfigError
from .loader import load_stack
from .models import StackDefinition

_STACK_FILENAMES = {
    "stack.yaml",
    "stack.yml",
    "stack.json",
}
_EXCLUDED_DIRS = {
    "tests",
    "tmp",
}


def discover_stacks(root: Path) -> dict[str, StackDefinition]:
    """Recursively discover all stack definition files under a root directory.

    Args:
        root: Root directory to search.

    Returns:
        Dict of stack name to `StackDefinition`.

    Raises:
        StacksmithConfigError: If duplicate stack names are found.
        FileNotFoundError: If root does not exist.
    """
    if not root.is_dir():
        raise FileNotFoundError(f"Root directory not found: {root}")

    stacks = {}
    duplicates = []

    for path in sorted(root.rglob("*")):
        if path.name in _STACK_FILENAMES and path.is_file():
            relative = path.relative_to(root)
            if any(part in _EXCLUDED_DIRS for part in relative.parts[:-1]):
                continue

            stack = load_stack(path)
            name = stack.name
            if name in stacks:
                duplicates.append(
                    f"  '{name}' defined in both {stacks[name].source_path} and {path}"
                )
            else:
                stacks[name] = stack

    if duplicates:
        raise StacksmithConfigError(
            f"Duplicate stack names found:\n {'\n'.join(duplicates)}"
        )

    return stacks


def filter_stacks_by_tags(
    stacks: dict[str, StackDefinition],
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
) -> dict[str, StackDefinition]:
    """Return stacks filtered by include / exclude tags.

    Args:
        stacks: Dict of stack name to `StackDefinition`.
        include_tags: If provided, only include stacks with at least one of these tags.
        exclude_tags: If provided, exclude stacks with any of these tags.

    Returns:
        Dict of stack name to `StackDefinition` after filtering.
    """
    include, exclude = map(set, (include_tags or [], exclude_tags or []))

    return {
        name: stack
        for name, stack in stacks.items()
        if (not include or include & stack.tags) and not (exclude & stack.tags)
    }


def build_dependency_graph(stacks: dict[str, StackDefinition]) -> dict[str, list[str]]:
    """Build an adjacency list representing the dependency graph.

    Args:
        stacks: Dict of stack name to `StackDefinition`.

    Returns:
        Dict of stack name to list of dependency names (stacks it depends on).

    Raises:
        StacksmithConfigError: If a stack references an unknown dependency.
    """
    graph = {}
    unknown = []

    for name, stack in stacks.items():
        graph[name] = list(stack.depends_on)
        for dep in stack.depends_on:
            if dep not in stacks:
                unknown.append(
                    f"  Stack '{name}' depends on '{dep}' which was not discovered"
                )

    if unknown:
        raise StacksmithConfigError(f"Unknown dependencies:\n {'\n'.join(unknown)}")

    return graph


def topological_sort(graph: dict[str, list[str]]) -> list[str]:
    """Return stacks in topological order (dependencies first).

    Args:
        graph: Adjacency list from `build_dependency_graph`.

    Returns:
        List of stack names in dependency order.

    Raises:
        StacksmithConfigError: If the graph contains cycles.
    """
    try:
        return list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise StacksmithConfigError("Circular dependencies detected") from exc
