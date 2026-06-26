from pathlib import Path

import pytest
from stacksmith.discovery import (
    build_dependency_graph,
    discover_stacks,
    filter_stacks_by_tags,
    topological_sort,
)
from stacksmith.exceptions import StacksmithConfigError
from stacksmith.models import ComponentDefinition, StackDefinition


def _stack(name: str, tags: list[str]) -> StackDefinition:
    return StackDefinition(
        name=name,
        tags=tags,
        depends_on=[],
        mock_outputs={},
        components={"r": ComponentDefinition(type="t")},
    )


class TestDiscoverStacks:
    def test_discovers_all_stacks(self, monorepo_dir: Path):
        stacks = discover_stacks(monorepo_dir)
        assert set(stacks.keys()) == {"vpc", "web", "rds"}

    def test_discovers_yaml_and_json(self, monorepo_dir: Path):
        stacks = discover_stacks(monorepo_dir)
        assert stacks["rds"].source_path.suffix == ".json"
        assert stacks["vpc"].source_path.suffix == ".yaml"

    def test_nonexistent_root_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            discover_stacks(tmp_path / "nonexistent")

    def test_duplicate_names_raises(self, tmp_path: Path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        for d in ("a", "b"):
            (tmp_path / d / "stack.yaml").write_text(
                "name: dupe\ncomponents:\n  r:\n    type: t\n"
            )
        with pytest.raises(StacksmithConfigError, match="Duplicate stack names"):
            discover_stacks(tmp_path)

    def test_excludes_stacks_under_default_ignored_directories(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "stack.yaml").write_text(
            "name: ignored-stack\ncomponents:\n  r:\n    type: t\n"
        )
        (tmp_path / "tmp").mkdir()
        (tmp_path / "tmp" / "stack.yaml").write_text(
            "name: ignored-stack-2\ncomponents:\n  r:\n    type: t\n"
        )
        stacks = discover_stacks(tmp_path)

        assert "ignored-stack" not in stacks
        assert "ignored-stack-2" not in stacks


class TestBuildDependencyGraph:
    def test_graph_structure(self, monorepo_dir: Path):
        stacks = discover_stacks(monorepo_dir)
        graph = build_dependency_graph(stacks)

        assert graph["vpc"] == []
        assert graph["web"] == ["vpc"]
        assert graph["rds"] == ["vpc"]

    def test_unknown_dependency_raises(self, tmp_path: Path):
        (tmp_path / "stack.yaml").write_text(
            "name: orphan\ndepends_on:\n  - nonexistent\ncomponents:\n  r:\n    type: t\n"
        )
        stacks = discover_stacks(tmp_path)
        with pytest.raises(StacksmithConfigError, match="Unknown dependencies"):
            build_dependency_graph(stacks)


class TestFilterStacksByTags:
    @pytest.mark.parametrize(
        ("stack_specs", "include_tags", "exclude_tags", "expected"),
        [
            (
                {"prod": ["prod", "db"], "dev": ["dev"]},
                ["prod"],
                None,
                {"prod"},
            ),
            (
                {"prod": ["prod", "db"], "experimental": ["experimental"]},
                None,
                ["experimental"],
                {"prod"},
            ),
            (
                {
                    "api": ["prod", "api"],
                    "worker": ["prod", "experimental"],
                },
                ["prod"],
                ["experimental"],
                {"api"},
            ),
        ],
    )
    def test_filters_by_tags(
        self,
        stack_specs: dict[str, list[str]],
        include_tags: list[str] | None,
        exclude_tags: list[str] | None,
        expected: set[str],
    ):
        stacks = {name: _stack(name, tags) for name, tags in stack_specs.items()}

        filtered = filter_stacks_by_tags(
            stacks,
            include_tags=include_tags,
            exclude_tags=exclude_tags,
        )

        assert set(filtered) == expected


class TestTopologicalSort:
    def test_correct_order(self, monorepo_dir: Path):
        stacks = discover_stacks(monorepo_dir)
        graph = build_dependency_graph(stacks)
        order = topological_sort(graph)

        assert order.index("vpc") < order.index("web")
        assert order.index("vpc") < order.index("rds")

    def test_cycle_raises(self):
        graph = {"a": ["b"], "b": ["c"], "c": ["a"]}
        with pytest.raises(StacksmithConfigError, match="Circular dependencies"):
            topological_sort(graph)
