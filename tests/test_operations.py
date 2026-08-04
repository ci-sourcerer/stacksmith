import io
import json
import runpy
import urllib.request
from pathlib import Path

import pytest

from stacksmith import api
from stacksmith.exceptions import StacksmithConfigError
from stacksmith.generation import generate_tf_json, write_tf_json
from stacksmith.loading import load_stack
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
            "default_module_mapping": {
                "source": {
                    "source": "registry",
                    "data": {
                        "address": "example/{{ component.type }}",
                        "version": "1.0.0",
                    },
                }
            },
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


def test_operation_descriptions_do_not_change_execution_identity():
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
    config = _config()
    original_identity = generate_tf_json(stack, config, {})["module"][
        "stacksmith_operation_deploy_app"
    ]["spec"]["identity"]
    config.operations["deploy"].description = "Deploy an approved release."
    config.operations["deploy"].inputs[
        "release_tag"
    ].description = "Immutable application release identifier."

    documented_identity = generate_tf_json(stack, config, {})["module"][
        "stacksmith_operation_deploy_app"
    ]["spec"]["identity"]

    assert documented_identity == original_identity


def test_operation_input_preserves_component_output_reference(tmp_path: Path):
    stack_file = tmp_path / "stack.yaml"
    stack_file.write_text(
        "name: application\n"
        "components:\n"
        "  app:\n"
        "    type: application\n"
        "operations:\n"
        "  deploy_app:\n"
        "    use: deploy\n"
        "    with:\n"
        '      release_tag: "production-{{ components.app.release_name }}"\n',
        encoding="utf-8",
    )
    stack = load_stack(
        stack_file,
        template_context={
            "inputs": {},
            "stack": {"name": "application", "tags": []},
        },
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
            },
            "outputs": {
                "release_name": {
                    "transform": {
                        "jinja": "release/{{ output.value }}",
                    }
                }
            },
        }
    )

    generated = generate_tf_json(stack, config, {"environment": "production"})

    module = generated["module"]["stacksmith_operation_deploy_app"]
    assert module["spec"]["environment"] == {
        "RELEASE_TAG": "production-release/${module.app.release_name}"
    }
    assert module["depends_on"] == ["module.app"]


def test_generates_selected_operation_with_transitive_dependencies():
    stack = StackDefinition.model_validate(
        {
            "name": "application",
            "operations": {
                "publish": {"use": "deploy", "with": {"release_tag": "1.2.3"}},
                "deploy": {
                    "use": "deploy",
                    "with": {"release_tag": "1.2.3"},
                    "depends_on": ["publish"],
                },
                "verify": {
                    "use": "deploy",
                    "with": {"release_tag": "1.2.3"},
                    "depends_on": ["deploy"],
                },
            },
        }
    )

    generated = generate_tf_json(
        stack,
        _config(),
        {},
        operation_names=["verify"],
    )

    assert list(generated["module"]) == [
        "stacksmith_operation_publish",
        "stacksmith_operation_deploy",
        "stacksmith_operation_verify",
    ]
    assert generated["module"]["stacksmith_operation_verify"]["depends_on"] == [
        "module.stacksmith_operation_deploy"
    ]


def test_rejects_unknown_and_cyclic_operation_dependencies():
    unknown_dependency = StackDefinition.model_validate(
        {
            "name": "application",
            "operations": {
                "deploy": {
                    "use": "deploy",
                    "depends_on": ["publish"],
                }
            },
        }
    )
    cyclic_dependency = StackDefinition.model_validate(
        {
            "name": "application",
            "operations": {
                "publish": {"use": "deploy", "depends_on": ["deploy"]},
                "deploy": {"use": "deploy", "depends_on": ["publish"]},
            },
        }
    )

    with pytest.raises(StacksmithConfigError, match="unknown operation 'publish'"):
        generate_tf_json(
            unknown_dependency,
            _config(),
            {},
            operation_names=["deploy"],
        )
    with pytest.raises(
        StacksmithConfigError,
        match="publish -> deploy -> publish",
    ):
        generate_tf_json(
            cyclic_dependency,
            _config(),
            {},
            operation_names=["publish"],
        )


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
    assert not (runner_dir / "jenkins.py").exists()


def test_skips_operation_runner_assets_when_no_operations_are_generated(tmp_path: Path):
    stack = StackDefinition.model_validate({"name": "application"})

    write_tf_json(stack, _config(), {}, tmp_path)

    assert not (tmp_path / ".stacksmith-operation-runner").exists()


def test_replaces_obsolete_operation_runner_assets(tmp_path: Path):
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
    config_data = _config().model_dump(mode="json")
    config_data["operations"]["deploy"] = {
        "runner": "jenkins",
        "trigger": "after_apply",
        "url": "https://jenkins.example.com",
        "job_name": "deploy-app",
        "username_env": "JENKINS_USERNAME",
        "api_token_env": "JENKINS_API_TOKEN",
        "parameters": {"RELEASE_TAG": "release_tag"},
        "inputs": {"release_tag": {"required": True}},
    }

    write_tf_json(stack, ToolConfig.model_validate(config_data), {}, tmp_path)
    write_tf_json(stack, _config(), {}, tmp_path)

    runner_dir = tmp_path / ".stacksmith-operation-runner"
    assert (runner_dir / "local.py").exists()
    assert not (runner_dir / "jenkins.py").exists()


def test_jenkins_operation_spec_configures_completion_polling():
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
    config_data = _config().model_dump(mode="json")
    config_data["operations"]["deploy"] = {
        "runner": "jenkins",
        "url": "https://jenkins.example.com",
        "job_name": "deploy-app",
        "username_env": "JENKINS_USERNAME",
        "api_token_env": "JENKINS_API_TOKEN",
        "poll_interval_seconds": 2,
        "timeout_seconds": 600,
        "parameters": {"RELEASE_TAG": "release_tag"},
        "inputs": {"release_tag": {"required": True}},
    }

    generated = generate_tf_json(
        stack,
        ToolConfig.model_validate(config_data),
        {},
        operation_names=["deploy_app"],
    )

    spec = generated["module"]["stacksmith_operation_deploy_app"]["spec"]
    assert spec["poll_interval_seconds"] == 2
    assert spec["timeout_seconds"] == 600


def test_local_operation_runner_echoes_spec_environment(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    monkeypatch.setenv(
        "STACKSMITH_OPERATION_SPEC",
        json.dumps(
            {
                "runner": "local",
                "command": [
                    "sh",
                    "-c",
                    'echo "Stacksmith simple GitOps reconciliation completed: environment=$STACKSMITH_OPERATION_ENVIRONMENT message=$STACKSMITH_OPERATION_MESSAGE project=$STACKSMITH_OPERATION_PROJECT"',
                ],
                "environment": {
                    "STACKSMITH_OPERATION_ENVIRONMENT": "dev",
                    "STACKSMITH_OPERATION_MESSAGE": "Hello from development",
                    "STACKSMITH_OPERATION_PROJECT": "stacksmith",
                },
                "working_directory": str(tmp_path),
            }
        ),
    )

    runner_path = (
        Path(__file__).parents[1] / "src/stacksmith/assets/operation_runner/local.py"
    )
    runpy.run_path(str(runner_path))

    assert capfd.readouterr().out == (
        "Stacksmith simple GitOps reconciliation completed: environment=dev "
        "message=Hello from development project=stacksmith\n"
    )


class _JenkinsResponse(io.BytesIO):
    def __init__(
        self,
        payload: dict[str, object] | None = None,
        *,
        status: int = 200,
        location: str | None = None,
    ) -> None:
        super().__init__(json.dumps(payload or {}).encode())
        self.status = status
        self.headers = {"Location": location} if location else {}


@pytest.mark.parametrize("result", ["SUCCESS", "FAILURE"])
def test_jenkins_operation_runner_waits_for_completed_build(
    monkeypatch: pytest.MonkeyPatch,
    result: str,
):
    requested_urls: list[str] = []

    def _fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        if request.full_url.endswith("/buildWithParameters"):
            return _JenkinsResponse(
                status=201,
                location="https://jenkins.example.com/queue/item/42/",
            )
        if request.full_url.endswith("/queue/item/42/api/json"):
            return _JenkinsResponse(
                {"executable": {"url": "https://jenkins.example.com/job/deploy/7/"}}
            )
        return _JenkinsResponse({"building": False, "result": result})

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setenv("JENKINS_USERNAME", "stacksmith")
    monkeypatch.setenv("JENKINS_API_TOKEN", "secret")
    monkeypatch.setenv(
        "STACKSMITH_OPERATION_SPEC",
        json.dumps(
            {
                "runner": "jenkins",
                "url": "https://jenkins.example.com",
                "job_name": "deploy",
                "username_env": "JENKINS_USERNAME",
                "api_token_env": "JENKINS_API_TOKEN",
                "parameters": {"RELEASE_TAG": "1.2.3"},
                "poll_interval_seconds": 0.01,
                "timeout_seconds": 30,
            }
        ),
    )

    runner_path = (
        Path(__file__).parents[1] / "src/stacksmith/assets/operation_runner/jenkins.py"
    )
    if result == "SUCCESS":
        runpy.run_path(str(runner_path))
    else:
        with pytest.raises(RuntimeError, match="result FAILURE"):
            runpy.run_path(str(runner_path))

    assert requested_urls == [
        "https://jenkins.example.com/job/deploy/buildWithParameters",
        "https://jenkins.example.com/queue/item/42/api/json",
        "https://jenkins.example.com/job/deploy/7/api/json",
    ]


@pytest.mark.parametrize("force_rerun", [False, True])
def test_run_single_stack_operation_passes_runtime_flags(
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
        "load_runtime_config",
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

    result = api.run_stack_operations(
        stack.source_path,
        ["deploy_app"],
        no_cas=True,
        force_rerun=force_rerun,
    )

    assert result == {
        "operations": ["deploy_app"],
        "execution_order": ["deploy_app"],
        "exit_code": 0,
    }
    expected_args = [
        "apply",
        "-target=module.stacksmith_operation_deploy_app",
        "-parallelism=10",
    ]
    if force_rerun:
        expected_args.append(
            "-replace=module.stacksmith_operation_deploy_app.terraform_data.operation"
        )
    assert calls["run"][0] == expected_args
    assert calls["run"][2]["no_cas"] is True


def test_plan_single_stack_operation_uses_targeted_dry_run(
    monkeypatch,
    tmp_path: Path,
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
        "load_runtime_config",
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

    result = api.plan_stack_operations(
        stack.source_path,
        ["deploy_app"],
        force_rerun=True,
    )

    assert result == {
        "operations": ["deploy_app"],
        "execution_order": ["deploy_app"],
        "exit_code": 0,
    }
    assert calls["run"][0] == [
        "plan",
        "-target=module.stacksmith_operation_deploy_app",
        "-parallelism=10",
        "-replace=module.stacksmith_operation_deploy_app.terraform_data.operation",
    ]
    assert calls["run"][2]["auto_approve"] is False


def test_run_stack_operations_uses_one_dependency_aware_apply(
    monkeypatch,
    tmp_path: Path,
):
    calls: dict[str, object] = {}
    monkeypatch.setenv("STACKSMITH_MAX_PARALLEL_OPERATIONS", "3")
    stack = StackDefinition.model_validate(
        {
            "name": "application",
            "operations": {
                "publish": {"use": "deploy", "with": {"release_tag": "1.2.3"}},
                "deploy": {
                    "use": "deploy",
                    "with": {"release_tag": "1.2.3"},
                    "depends_on": ["publish"],
                },
                "docs": {"use": "deploy", "with": {"release_tag": "1.2.3"}},
            },
        }
    )
    stack.source_path = tmp_path / "stack.yaml"
    monkeypatch.setattr(
        api,
        "load_runtime_config",
        lambda *args, **kwargs: (tmp_path, [], _config()),
    )
    monkeypatch.setattr(
        api,
        "_prepare_stack_definition",
        lambda *args, **kwargs: (stack, {}),
    )

    def _fake_generate(*args, **kwargs):
        calls["generated_operations"] = kwargs["operation_names"]
        return tmp_path / "build"

    def _fake_run(args, working_dir, **kwargs):
        calls["run"] = (args, working_dir, kwargs)
        return 0

    monkeypatch.setattr(api, "_generate_single_stack", _fake_generate)
    monkeypatch.setattr(api, "run_terragrunt", _fake_run)

    result = api.run_stack_operations(
        stack.source_path,
        ["deploy", "docs"],
        force_rerun=True,
    )

    assert result == {
        "operations": ["deploy", "docs"],
        "execution_order": ["publish", "deploy", "docs"],
        "exit_code": 0,
    }
    assert calls["generated_operations"] == ["publish", "deploy", "docs"]
    assert calls["run"][0] == [
        "apply",
        "-target=module.stacksmith_operation_deploy",
        "-target=module.stacksmith_operation_docs",
        "-parallelism=3",
        "-replace=module.stacksmith_operation_deploy.terraform_data.operation",
        "-replace=module.stacksmith_operation_docs.terraform_data.operation",
    ]
