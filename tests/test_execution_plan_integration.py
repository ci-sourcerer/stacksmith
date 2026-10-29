import json
import shutil
from pathlib import Path

import pytest
import yaml
from cryptography.fernet import Fernet

from stacksmith import api, runner
from stacksmith.ci.contracts import CiExecutionManifest, CiExecutionRow
from stacksmith.cli import main as cli_main
from stacksmith.execution_plans import transfer_execution_plan


def _write_named_stack(root: Path, name: str, value: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "stack.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "components": {
                    "item": {"type": "data", "properties": {"input": value}}
                },
            }
        )
    )


@pytest.mark.skipif(
    not shutil.which("tofu") or not shutil.which("terragrunt"),
    reason="Requires local OpenTofu and Terragrunt binaries",
)
def test_run_all_applies_reviewed_values_after_source_changes(tmp_path, monkeypatch):
    """Exercise real per-stack binaries so forwarding-only mocks cannot hide replanning."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STACKSMITH_SKIP_TOOL_VERSION_CHECK", "1")
    monkeypatch.setattr(runner, "_TOOL_VERSION_CHECKED", False)
    (tmp_path / "module").mkdir()
    (tmp_path / "module/main.tf").write_text(
        'variable "input" { type = string }\n'
        'resource "terraform_data" "reviewed" { input = var.input }\n'
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "backend": {"data": {"type": "local", "path": str(tmp_path / "state")}},
                "default_module_mapping": {
                    "source": {"source": "local", "data": {"path": "module"}}
                },
            }
        )
    )
    for name in ("app", "worker"):
        _write_named_stack(tmp_path / "stacks", name, f"{name}-reviewed")
    assert (
        api.run_all_stacks(
            "plan",
            tmp_path / "stacks",
            config=["config.yaml"],
            out=Path("reviewed plans"),
            plan_summary="none",
        )
        == 0
    )
    for name in ("app", "worker"):
        assert (tmp_path / "reviewed plans" / f"{name}.tfplan").is_file()
        _write_named_stack(tmp_path / "stacks", name, f"{name}-unreviewed")
    assert (
        api.run_all_stacks(
            "apply",
            tmp_path / "stacks",
            config=["config.yaml"],
            plan=Path("reviewed plans"),
            apply_summary="none",
            auto_approve=True,
        )
        == 0
    )
    for name in ("app", "worker"):
        assert (
            json.loads((tmp_path / "state" / name / "terraform.tfstate").read_text())[
                "resources"
            ][0]["instances"][0]["attributes"]["input"]["value"]
            == f"{name}-reviewed"
        )


@pytest.mark.skipif(
    not shutil.which("tofu") or not shutil.which("terragrunt"),
    reason="Requires local OpenTofu and Terragrunt binaries",
)
@pytest.mark.parametrize(
    "plan_path", ["reviewed.tfplan", "saved plans/reviewed.tfplan"]
)
def test_relative_plan_path_applies_the_saved_binary(tmp_path, monkeypatch, plan_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STACKSMITH_SKIP_TOOL_VERSION_CHECK", "1")
    monkeypatch.setattr(runner, "_TOOL_VERSION_CHECKED", False)
    (tmp_path / "module").mkdir()
    (tmp_path / "module/main.tf").write_text(
        'resource "terraform_data" "reviewed" { input = "reviewed value" }\n'
    )
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "backend": {"data": {"type": "local", "path": str(tmp_path / "state")}},
                "default_module_mapping": {
                    "source": {"source": "local", "data": {"path": "module"}}
                },
            }
        )
    )
    (tmp_path / "stack.yaml").write_text(
        "name: app\ncomponents:\n  item:\n    type: data\n"
    )
    assert (
        api.run_stack_action(
            "plan",
            Path("stack.yaml"),
            config=["config.yaml"],
            out=Path(plan_path),
            plan_summary="none",
        )
        == 0
    )
    assert Path(plan_path).is_file()
    assert (
        api.run_stack_action(
            "apply",
            Path("stack.yaml"),
            config=["config.yaml"],
            plan=Path(plan_path),
            apply_summary="none",
            skip_after_apply=True,
        )
        == 0
    )
    assert (
        json.loads((tmp_path / "state/app/terraform.tfstate").read_text())["resources"][
            0
        ]["instances"][0]["attributes"]["input"]["value"]
        == "reviewed value"
    )


@pytest.mark.skipif(
    not shutil.which("tofu") or not shutil.which("terragrunt"),
    reason="Requires local OpenTofu and Terragrunt binaries",
)
def test_reviewed_apply_and_destroy_across_workspaces(tmp_path: Path, monkeypatch):
    source = tmp_path / "preview"
    destination = tmp_path / "execution"
    source.mkdir()
    destination.mkdir()
    (source / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "backend": {"data": {"type": "local", "path": str(source / "state")}},
                "module_mappings": {
                    "data": {
                        "source": {
                            "source": "local",
                            "data": {
                                "path": str(
                                    Path(__file__).parents[1]
                                    / "examples/modules/terraform_data"
                                ),
                            },
                        },
                        "properties": {"input": {}},
                    }
                },
            }
        )
    )
    (source / "stack.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "reviewed",
                "components": {
                    "item": {
                        "type": "data",
                        "properties": {"input": {"value": "reviewed"}},
                    }
                },
            }
        )
    )
    (source / "run.yaml").write_text(
        yaml.safe_dump(
            {
                "stacks": [
                    {"source": "local", "data": {"path": str(source / "stack.yaml")}}
                ],
            }
        )
    )
    manifest = CiExecutionManifest(
        command="apply",
        config_ref=str(source / "config.yaml"),
        matrix=[CiExecutionRow(environment="test", runfile=str(source / "run.yaml"))],
    )
    monkeypatch.setenv("STACKSMITH_PLAN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("STACKSMITH_CI_EXECUTION_ID", "integration")
    monkeypatch.chdir(source)
    assert cli_main._execute_ci_manifest(manifest, "test", "plan") == 0
    binding = cli_main._ci_plan_binding(manifest, "test")
    transfer_execution_plan(
        Path(".stacksmith-ci/test/reviewed.tfplan"), binding, decrypt=False
    )
    target = destination / ".stacksmith-ci/test"
    target.mkdir(parents=True)
    shutil.copyfile(
        source / ".stacksmith-ci/test/reviewed.enc", target / "reviewed.enc"
    )
    original_stack = (source / "stack.yaml").read_text()
    (source / "stack.yaml").write_text("invalid source written after preview")
    monkeypatch.chdir(destination)
    transfer_execution_plan(
        Path(".stacksmith-ci/test/reviewed.tfplan"), binding, decrypt=True
    )
    assert cli_main._execute_ci_manifest(manifest, "test", "apply") == 0
    state = next((source / "state").rglob("terraform.tfstate"))
    assert json.loads(state.read_text())["resources"][0]["instances"][0]["attributes"][
        "input"
    ]["value"] == {"value": "reviewed"}
    assert not (target / "reviewed.tfplan").exists()
    (source / "stack.yaml").write_text(original_stack)
    manifest = manifest.model_copy(update={"command": "destroy"})
    assert cli_main._execute_ci_manifest(manifest, "test", "plan") == 0
    assert cli_main._execute_ci_manifest(manifest, "test", "destroy") == 0
    assert not json.loads(state.read_text())["resources"]
