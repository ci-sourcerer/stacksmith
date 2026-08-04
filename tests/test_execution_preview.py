import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from stacksmith import api
from stacksmith.cli import main as cli_main
from stacksmith.cli.parser import build_parser
from stacksmith.enums import TerragruntAction
from stacksmith.execution import build_execution_preview
from stacksmith.graph import (
    render_execution_preview_dot,
    render_execution_preview_mermaid,
)
from stacksmith.loading import load_config
from stacksmith.models import (
    ComponentDefinition,
    ExcludedStackPreview,
    ExecutionPreview,
    StackDefinition,
)


def _stacks(root: Path) -> dict[str, StackDefinition]:
    return {
        "vpc": StackDefinition(
            name="vpc",
            outputs={
                "subnet_ids": {
                    "value": [],
                    "mock": ["mock-subnet"],
                },
                "vpc_id": {
                    "value": "vpc",
                    "mock": "mock-vpc",
                },
            },
            components={
                "network": ComponentDefinition(
                    type="aws_s3_bucket",
                )
            },
            source_path=root / "networking" / "vpc" / "stack.yaml",
        ),
        "web": StackDefinition(
            name="web",
            depends_on=["vpc"],
            components={
                "server": ComponentDefinition(
                    type="aws_ec2_instance",
                    tags={"prod"},
                )
            },
            source_path=root / "compute" / "web" / "stack.yaml",
        ),
    }


def _build_dirs(root: Path) -> dict[str, Path]:
    return {
        "vpc": root / ".stacksmith" / "networking" / "vpc",
        "web": root / ".stacksmith" / "compute" / "web",
    }


def test_build_execution_preview_includes_graph_metadata(
    tmp_path: Path,
    sample_config_yaml: Path,
):
    preview = build_execution_preview(
        "plan",
        tmp_path,
        _stacks(tmp_path),
        _build_dirs(tmp_path),
        load_config(sample_config_yaml),
        state_root=tmp_path,
    )

    assert preview.execution_order == ["vpc", "web"]
    assert preview.would_clean is False
    assert preview.stacks[0].state_key == "networking/vpc/terraform.tfstate"
    assert preview.stacks[0].selected_components == ["network"]
    assert preview.stacks[0].command == ["terragrunt", "plan"]
    assert preview.stacks[1].dependencies[0].name == "vpc"
    assert preview.stacks[1].dependencies[0].uses_mock_outputs is True
    assert preview.stacks[1].dependencies[0].mock_output_keys == [
        "subnet_ids",
        "vpc_id",
    ]


def test_build_execution_preview_reverses_destroy_order(
    tmp_path: Path,
    sample_config_yaml: Path,
):
    preview = build_execution_preview(
        TerragruntAction.DESTROY,
        tmp_path,
        _stacks(tmp_path),
        _build_dirs(tmp_path),
        load_config(sample_config_yaml),
        state_root=tmp_path,
        auto_approve=True,
        no_cas=True,
        clean=True,
    )

    assert preview.execution_order == ["web", "vpc"]
    assert preview.would_clean is True
    assert preview.stacks[0].execution_position == 2
    assert preview.stacks[1].execution_position == 1
    assert preview.stacks[1].command == [
        "terragrunt",
        "--no-cas",
        "destroy",
        "--auto-approve",
    ]
    assert preview.stacks[1].dependencies[0].uses_mock_outputs is False


def test_build_execution_preview_reports_component_selection(
    tmp_path: Path,
    sample_config_yaml: Path,
):
    preview = build_execution_preview(
        "plan",
        tmp_path,
        _stacks(tmp_path),
        _build_dirs(tmp_path),
        load_config(sample_config_yaml),
        state_root=tmp_path,
        tags=["prod"],
    )

    assert preview.execution_order == ["web"]
    assert preview.stacks[0].selected is False
    assert preview.stacks[0].selected_components == []
    assert preview.stacks[0].command == []
    assert preview.stacks[0].skip_reason is not None
    assert preview.stacks[1].selected_components == ["server"]
    assert preview.stacks[1].terragrunt_args == [
        "plan",
        "-target",
        "module.server",
    ]


def test_execution_preview_serializes_paths_and_action(
    tmp_path: Path,
    sample_config_yaml: Path,
):
    preview = build_execution_preview(
        "apply",
        tmp_path,
        _stacks(tmp_path),
        _build_dirs(tmp_path),
        load_config(sample_config_yaml),
        state_root=tmp_path,
        excluded_stacks=[
            ExcludedStackPreview(
                name="dev",
                source_path=tmp_path / "dev" / "stack.yaml",
                reason="Did not match an include tag.",
            )
        ],
    )

    payload = json.loads(preview.model_dump_json())

    assert payload["schema_version"] == 1
    assert payload["action"] == "apply"
    assert payload["root"] == str(tmp_path)
    assert payload["excluded_stacks"][0]["name"] == "dev"


def test_graph_renderers_show_execution_flow_and_mock_outputs(
    tmp_path: Path,
    sample_config_yaml: Path,
):
    preview = build_execution_preview(
        "plan",
        tmp_path,
        _stacks(tmp_path),
        _build_dirs(tmp_path),
        load_config(sample_config_yaml),
        state_root=tmp_path,
    )

    dot = render_execution_preview_dot(preview)
    mermaid = render_execution_preview_mermaid(preview)

    assert '"vpc" -> "web" [label="mock outputs: subnet_ids, vpc_id"];' in dot
    assert "1. vpc\\npath:" in dot
    assert "flowchart LR" in mermaid
    assert "stack_0 -->|mock outputs: subnet_ids, vpc_id| stack_1" in mermaid
    assert "1. vpc<br/>path:" in mermaid


def test_run_all_dry_run_validates_without_writing_or_executing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_config_yaml: Path,
):
    root = tmp_path / "repo"
    (root / "network").mkdir(parents=True)
    (root / "application").mkdir()
    (root / "network" / "stack.yaml").write_text(
        "name: network\n"
        "components:\n"
        "  state:\n"
        "    type: aws_s3_bucket\n"
        "outputs:\n"
        "  bucket_id:\n"
        '    value: "{{ components.state.bucket_id }}"\n'
        "    mock: mock-bucket\n",
        encoding="utf-8",
    )
    (root / "application" / "stack.yaml").write_text(
        "name: application\n"
        "depends_on:\n"
        "  - network\n"
        "components:\n"
        "  server:\n"
        "    type: aws_ec2_instance\n",
        encoding="utf-8",
    )
    build_dir = root / "build"
    build_dir.mkdir()
    marker = build_dir / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    monkeypatch.setattr(
        api,
        "run_terragrunt_all_ordered",
        lambda *_args, **_kwargs: pytest.fail("Terragrunt runner was invoked"),
    )

    result = api.run_all_stacks(
        "plan",
        root,
        config=[str(sample_config_yaml)],
        build_dir=build_dir,
        clean=True,
        dry_run=True,
    )

    assert isinstance(result, ExecutionPreview)
    assert result.execution_order == ["network", "application"]
    assert result.would_clean is True
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (build_dir / "network" / "stacksmith.tf.json").exists()


def test_run_all_dry_run_propagates_static_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_config_yaml: Path,
):
    (tmp_path / "stack.yaml").write_text(
        "name: invalid\ncomponents:\n  resource:\n    type: aws_s3_bucket\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        api,
        "generate_tf_json",
        Mock(side_effect=api.StacksmithConfigError("static validation failed")),
    )

    with pytest.raises(api.StacksmithConfigError, match="static validation failed"):
        api.run_all_stacks(
            "plan",
            tmp_path,
            config=[str(sample_config_yaml)],
            dry_run=True,
        )


def test_run_all_dry_run_rejects_plan_dependent_options(tmp_path: Path):
    with pytest.raises(
        api.StacksmithConfigError,
        match="--dry-run cannot be combined",
    ):
        api.run_all_stacks(
            "plan",
            tmp_path,
            dry_run=True,
            save_plan_json=tmp_path / "plans",
        )


def test_inspect_dependency_graph_reports_stack_filter_exclusions(
    tmp_path: Path,
    sample_config_yaml: Path,
):
    for name, tag in (("production", "prod"), ("development", "dev")):
        stack_dir = tmp_path / name
        stack_dir.mkdir()
        (stack_dir / "stack.yaml").write_text(
            f"name: {name}\n"
            "tags:\n"
            f"  - {tag}\n"
            "components:\n"
            "  resource:\n"
            "    type: aws_s3_bucket\n",
            encoding="utf-8",
        )

    preview = api.inspect_dependency_graph(
        tmp_path,
        config=[str(sample_config_yaml)],
        include_tags=["prod"],
    )

    assert preview.execution_order == ["production"]
    assert preview.excluded_stacks[0].name == "development"
    assert "include tag" in preview.excluded_stacks[0].reason


def test_parser_supports_execution_preview_formats():
    parser = build_parser()

    run_all_args = parser.parse_args(
        ["run-all", "plan", "--dry-run", "--format", "json"]
    )
    graph_args = parser.parse_args(
        ["info", "graph", "--action", "destroy", "--format", "mermaid"]
    )

    assert run_all_args.dry_run is True
    assert run_all_args.format == "json"
    assert graph_args.action == "destroy"
    assert graph_args.format == "mermaid"

    with pytest.raises(SystemExit):
        parser.parse_args(["run-all", "plan", "--format", "dot"])


def test_cmd_run_all_rejects_preview_format_without_dry_run():
    args = build_parser().parse_args(["run-all", "plan", "--format", "json"])

    assert cli_main._cmd_run_all(args) == 1


def test_cmd_run_all_emits_dry_run_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_config_yaml: Path,
    capsys: pytest.CaptureFixture[str],
):
    preview = build_execution_preview(
        "plan",
        tmp_path,
        _stacks(tmp_path),
        _build_dirs(tmp_path),
        load_config(sample_config_yaml),
        state_root=tmp_path,
    )
    calls: dict[str, object] = {}

    def _fake_run_all_stacks(action, root, **kwargs):
        calls["args"] = (action, root, kwargs)
        return preview

    monkeypatch.setattr(cli_main, "run_all_stacks", _fake_run_all_stacks)
    args = build_parser().parse_args(
        [
            "run-all",
            "plan",
            "--root",
            str(tmp_path),
            "--dry-run",
            "--format",
            "json",
        ]
    )

    exit_code = cli_main._cmd_run_all(args)

    assert exit_code == 0
    assert calls["args"][2]["dry_run"] is True
    assert json.loads(capsys.readouterr().out)["execution_order"] == ["vpc", "web"]


def test_cmd_info_graph_emits_dot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_config_yaml: Path,
    capsys: pytest.CaptureFixture[str],
):
    preview = build_execution_preview(
        "plan",
        tmp_path,
        _stacks(tmp_path),
        _build_dirs(tmp_path),
        load_config(sample_config_yaml),
        state_root=tmp_path,
    )
    calls: dict[str, object] = {}

    def _fake_inspect_dependency_graph(root, **kwargs):
        calls["args"] = (root, kwargs)
        return preview

    monkeypatch.setattr(
        cli_main,
        "inspect_dependency_graph",
        _fake_inspect_dependency_graph,
    )
    args = build_parser().parse_args(
        [
            "info",
            "graph",
            "--root",
            str(tmp_path),
            "--format",
            "dot",
        ]
    )

    exit_code = cli_main._cmd_info_graph(args)

    assert exit_code == 0
    assert calls["args"][1]["action"] == "plan"
    assert capsys.readouterr().out.startswith("digraph stacksmith {")
