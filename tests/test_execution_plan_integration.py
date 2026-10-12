import json
import shutil
from pathlib import Path

import pytest
import yaml
from cryptography.fernet import Fernet

from stacksmith.ci.contracts import CiExecutionManifest, CiExecutionRow
from stacksmith.cli import main as cli_main
from stacksmith.execution_plans import transfer_execution_plan


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
