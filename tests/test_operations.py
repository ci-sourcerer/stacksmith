from pathlib import Path

import pytest
from stacksmith import api
from stacksmith.generator import generate_tf_json, write_tf_json
from stacksmith.models import ModuleMapping, StackDefinition, ToolConfig


def _config() -> ToolConfig:
    return ToolConfig.model_validate(
        {
            "backend": {"type": "local", "path": ".state"},
            "tools": {
                "tofu": {"version": "1.11.6"},
                "terragrunt": {"version": "1.0.6"},
            },
            "provider_mappings": {},
            "module_mappings": {},
            "operations": {
                "deploy": {
                    "runner": "local",
                    "trigger": "after_apply",
                    "command": ["deploy"],
                    "environment": {"RELEASE_TAG": "release_tag"},
                    "inputs": {"release_tag": {"required": True}},
                }
            },
        }
    )


def test_generates_after_apply_operation_module():
    stack = StackDefinition.model_validate(
        {
            "name": "application",
            "operations": {
                "deploy_app": {
                    "use": "deploy",
                    "with": {"release_tag": "1.2.3"},
                }
            },
        }
    )

    generated = generate_tf_json(stack, _config(), {"release_tag": "1.2.3"})

    module = generated["module"]["stacksmith_operation_deploy_app"]
    assert module["source"] == "./.stacksmith-operation-runner"
    assert module["spec"]["runner"] == "local"
    assert module["spec"]["environment"] == {"RELEASE_TAG": "1.2.3"}


def test_operation_input_preserves_component_output_reference():
    stack = StackDefinition.model_validate(
        {
            "name": "application",
            "components": {"app": {"type": "application"}},
            "operations": {
                "deploy_app": {
                    "use": "deploy",
                    "with": {"release_tag": "production-${module.app.release_name}"},
                }
            },
        }
    )
    config = _config()
    config.module_mappings["application"] = ModuleMapping.model_validate(
        {
            "source": {
                "source": "registry",
                "data": {
                    "address": "example/application",
                    "version": "1.0.0",
                },
            }
        }
    )

    generated = generate_tf_json(stack, config, {"environment": "production"})

    module = generated["module"]["stacksmith_operation_deploy_app"]
    assert module["spec"]["environment"] == {
        "RELEASE_TAG": "production-${module.app.release_name}"
    }
    assert module["depends_on"] == ["${module.app}"]


def test_writes_packaged_operation_runner_assets(tmp_path: Path):
    stack = StackDefinition.model_validate(
        {
            "name": "application",
            "operations": {
                "deploy_app": {
                    "use": "deploy",
                    "with": {"release_tag": "1.2.3"},
                }
            },
        }
    )

    write_tf_json(stack, _config(), {}, tmp_path)

    runner_dir = tmp_path / ".stacksmith-operation-runner"
    assert "terraform_data" in (runner_dir / "main.tf").read_text()
    assert "subprocess.run" in (runner_dir / "local.py").read_text()
    assert "urlopen" in (runner_dir / "jenkins.py").read_text()


@pytest.mark.parametrize("force_rerun", [False, True])
def test_run_stack_operation_passes_runtime_flags(
    monkeypatch,
    tmp_path: Path,
    force_rerun: bool,
):
    calls: dict[str, object] = {}
    stack = StackDefinition.model_validate(
        {
            "name": "application",
            "operations": {
                "deploy_app": {
                    "use": "deploy",
                    "with": {"release_tag": "1.2.3"},
                }
            },
        }
    )
    stack.source_path = tmp_path / "stack.yaml"

    monkeypatch.setattr(
        api,
        "_load_runtime_config",
        lambda *args, **kwargs: (tmp_path, [], _config()),
    )
    monkeypatch.setattr(
        api,
        "_prepare_stack_definition",
        lambda *args, **kwargs: (stack, {}),
    )
    monkeypatch.setattr(
        api,
        "_generate_single_stack",
        lambda *args, **kwargs: tmp_path / "build",
    )

    def _fake_run_terragrunt(args, working_dir, **kwargs):
        calls["run"] = (args, working_dir, kwargs)
        return 0

    monkeypatch.setattr(api, "run_terragrunt", _fake_run_terragrunt)

    result = api.run_stack_operation(
        stack.source_path,
        "deploy_app",
        no_cas=True,
        force_rerun=force_rerun,
    )

    assert result == {"operation": "deploy_app", "exit_code": 0}
    expected_args = [
        "apply",
        "-target=module.stacksmith_operation_deploy_app",
    ]
    if force_rerun:
        expected_args.append(
            "-replace=module.stacksmith_operation_deploy_app.terraform_data.operation"
        )
    assert calls["run"][0] == expected_args
    assert calls["run"][2]["no_cas"] is True
