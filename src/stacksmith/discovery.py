from pathlib import Path

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
        ValueError: If duplicate stack names are found.
        FileNotFoundError: If root does not exist.
    """
    if not root.is_dir():
        raise FileNotFoundError(f"Root directory not found: {root}")

    stacks: dict[str, StackDefinition] = {}
    duplicates: list[str] = []

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
        raise ValueError(f"Duplicate stack names found:\n {'\n'.join(duplicates)}")

    return stacks


def filter_stacks_by_tags(
    stacks: dict[str, StackDefinition],
    include_tags: list[str] | None = None,
    exclude_tags: list[str] | None = None,
) -> dict[str, StackDefinition]:
    """Return stacks filtered by include / exclude tags."""
    include = set(include_tags or [])
    exclude = set(exclude_tags or [])

    if include:
        stacks = {
            name: stack
            for name, stack in stacks.items()
            if include.intersection(stack.tags)
        }

    if exclude:
        stacks = {
            name: stack
            for name, stack in stacks.items()
            if not exclude.intersection(stack.tags)
        }

    return stacks


def build_dependency_graph(stacks: dict[str, StackDefinition]) -> dict[str, list[str]]:
    """Build an adjacency list representing the dependency graph.

    Args:
        stacks: Dict of stack name to `StackDefinition`.

    Returns:
        Dict of stack name to list of dependency names (stacks it depends on).

    Raises:
        ValueError: If a stack references an unknown dependency.
    """
    graph: dict[str, list[str]] = {}
    unknown: list[str] = []

    for name, stack in stacks.items():
        graph[name] = list(stack.depends_on)
        for dep in stack.depends_on:
            if dep not in stacks:
                unknown.append(
                    f"  Stack '{name}' depends on '{dep}' which was not discovered"
                )

    if unknown:
        raise ValueError(f"Unknown dependencies:\n {'\n'.join(unknown)}")

    return graph


def detect_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Detect cycles in the dependency graph using DFS.

    Args:
        graph: Adjacency list from `build_dependency_graph`.

    Returns:
        List of cycles found. Each cycle is a list of stack names forming the loop.
        Empty list if no cycles exist.
    """
    visited: set[str] = set()
    in_stack: set[str] = set()
    path: list[str] = []
    cycles: list[list[str]] = []

    def _dfs(node: str) -> None:
        visited.add(node)
        in_stack.add(node)
        path.append(node)

        for neighbor in graph.get(node, []):
            if neighbor in in_stack:
                cycle_start = path.index(neighbor)
                cycles.append(path[cycle_start:] + [neighbor])
            elif neighbor not in visited:
                _dfs(neighbor)

        path.pop()
        in_stack.discard(node)

    for node in graph:
        if node not in visited:
            _dfs(node)

    return cycles


def topological_sort(graph: dict[str, list[str]]) -> list[str]:
    """Return stacks in topological order (dependencies first).

    Args:
        graph: Adjacency list from `build_dependency_graph`.

    Returns:
        List of stack names in dependency order.

    Raises:
        ValueError: If the graph contains cycles.
    """
    cycles = detect_cycles(graph)
    if cycles:
        cycle_strs = [" -> ".join(c) for c in cycles]
        raise ValueError(
            f"Circular dependencies detected:\n  {'\n  '.join(cycle_strs)}"
        )

    visited: set[str] = set()
    order: list[str] = []

    def _visit(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        for dep in graph.get(node, []):
            _visit(dep)
        order.append(node)

    for node in graph:
        _visit(node)

    return order


def format_graph(stacks: dict[str, StackDefinition]) -> str:
    """Format the dependency graph as a human-readable string.

    Args:
        stacks: Dict of stack name to `StackDefinition`.

    Returns:
        Multi-line string showing each stack and its dependencies.
    """
    graph = build_dependency_graph(stacks)
    order = topological_sort(graph)
    lines: list[str] = []

    for name in order:
        deps = graph.get(name, [])
        if deps:
            lines.append(f"  {name} -> {', '.join(deps)}")
        else:
            lines.append(f"  {name} (no dependencies)")

    return f"Dependency graph (topological order):\n{'\n'.join(lines)}"
