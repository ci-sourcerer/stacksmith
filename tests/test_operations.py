import io
import json
import runpy
import subprocess
import urllib.request
from pathlib import Path

import pytest

from stacksmith import api
from stacksmith.exceptions import StacksmithConfigError
from stacksmith.generation import (
    build_operation_module_spec,
    generate_operations_tf_json,
    generate_tf_json,
    write_operations_tf_json,
    write_tf_json,
)
from stacksmith.loading import load_stack
from stacksmith.models import ModuleMapping, StackDefinition, ToolConfig


def _config() -> ToolConfig:
    return ToolConfig.model_validate(
        {
            "backend": {
                "data": {
                    "type": "local",
                    "path": ".state",
                }
            },
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
                    "environment": {
                        "mode": "auto",
                        "inputs": {"overrides": {"RELEASE_TAG": "release_tag"}},
                    },
                    "inputs": {"release_tag": {"required": True}},
                }
            },
        }
    )


def test_local_operation_environment_auto_maps_inputs_with_overrides_and_exclusions():
    config = ToolConfig.model_validate(
        {
            "backend": {"data": {"type": "local", "path": ".state"}},
            "default_module_mapping": {
                "source": {
                    "source": "registry",
                    "data": {"address": "example/module", "version": "1.0.0"},
                }
            },
            "operations": {
                "check": {
                    "runner": "local",
                    "command": ["check"],
                    "environment": {
                        "mode": "auto",
                        "inputs": {
                            "overrides": {"KUBE_CONTEXT": "kubeconfig_context"},
                            "exclude": ["excluded"],
                        },
                    },
                    "inputs": {
                        "stack_name": {},
                        "kubeconfig_context": {},
                        "excluded": {},
                    },
                }
            },
        }
    )

    stack = StackDefinition.model_validate(
        {
            "name": "check",
            "operations": {
                "run": {
                    "use": "check",
                    "with": {
                        "stack_name": "stack",
                        "kubeconfig_context": "k3s",
                        "excluded": "secret",
                    },
                }
            },
        }
    )

    assert build_operation_module_spec(stack, config, "run")["environment"] == {
        "STACK_NAME": "stack",
        "KUBE_CONTEXT": "k3s",
    }


def _write_safe_operation_plan(kwargs: dict[str, object]) -> None:
    plan_path = kwargs.get("save_plan_json")
    if isinstance(plan_path, Path):
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(
                {
                    "resource_changes": [
                        {
                            "address": (
                                "module.stacksmith_operation_deploy_app."
                                "terraform_data.operation"
                            ),
                            "mode": "managed",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )


def test_operation_plan_rejects_managed_infrastructure_changes(tmp_path: Path):
    plan_path = tmp_path / "operation-plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "resource_changes": [
                    {
                        "address": "module.application.aws_instance.main",
                        "mode": "managed",
                    },
                    {
                        "address": "data.terraform_remote_state.infrastructure",
                        "mode": "data",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        StacksmithConfigError,
        match="outside operation modules: module.application.aws_instance.main",
    ):
        api._validate_operation_plan(plan_path)


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

    generated = generate_operations_tf_json(stack, _config())

    module = generated["module"]["stacksmith_operation_deploy_app"]
    assert module["source"] == "./.stacksmith-operation-runner"
    assert module["runner"] == "local"
    assert module["spec"]["runner"] == "local"
    assert module["spec"]["environment"] == {"RELEASE_TAG": "1.2.3"}
    assert module["spec"]["stream_output"] is False


def test_streams_output_for_an_operation_without_secret_inputs():
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
    config.operations["deploy"].stream_output = True

    generated = generate_operations_tf_json(stack, config)

    module = generated["module"]["stacksmith_operation_deploy_app"]
    assert module["spec"]["stream_output"] is True


def test_rejects_streaming_output_for_an_operation_with_secret_inputs():
    config_data = _config().model_dump(mode="json")
    config_data["operations"]["deploy"]["stream_output"] = True
    config_data["operations"]["deploy"]["inputs"]["release_tag"]["secret"] = True

    with pytest.raises(
        ValueError,
        match="Operation output masking must include secret inputs",
    ):
        ToolConfig.model_validate(config_data)


def test_streaming_output_allows_secret_input_when_configured_for_masking():
    stack = StackDefinition.model_validate(
        {
            "name": "application",
            "operations": {
                "deploy_app": {
                    "use": "deploy",
                    "with": {"release_tag": "1.2.3-secret"},
                }
            },
        }
    )
    config_data = _config().model_dump(mode="json")
    config_data["operations"]["deploy"]["stream_output"] = True
    config_data["operations"]["deploy"]["inputs"]["release_tag"]["secret"] = True
    config_data["operations"]["deploy"]["output_masking"] = {
        "inputs": ["release_tag"],
        "literals": ["DO-NOT-LEAK"],
    }

    generated = generate_operations_tf_json(
        stack, ToolConfig.model_validate(config_data)
    )

    module = generated["module"]["stacksmith_operation_deploy_app"]
    assert module["spec"]["stream_output"] is True
    assert module["spec"]["mask_literals"] == ["DO-NOT-LEAK", "1.2.3-secret"]


def test_rejects_streaming_output_for_a_jenkins_operation():
    config_data = _config().model_dump(mode="json")
    config_data["operations"]["deploy"] = {
        "runner": "jenkins",
        "url": "https://jenkins.example.com",
        "job_name": "deploy-app",
        "username_env": "JENKINS_USERNAME",
        "api_token_env": "JENKINS_API_TOKEN",
        "parameters": {"RELEASE_TAG": "release_tag"},
        "inputs": {"release_tag": {"required": True}},
        "stream_output": True,
    }

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        ToolConfig.model_validate(config_data)


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
    original_identity = generate_operations_tf_json(stack, config)["module"][
        "stacksmith_operation_deploy_app"
    ]["spec"]["identity"]
    config.operations["deploy"].description = "Deploy an approved release."
    config.operations["deploy"].inputs[
        "release_tag"
    ].description = "Immutable application release identifier."

    documented_identity = generate_operations_tf_json(stack, config)["module"][
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

    infrastructure = generate_tf_json(stack, config, {"environment": "production"})
    generated = generate_operations_tf_json(stack, config)

    module = generated["module"]["stacksmith_operation_deploy_app"]
    assert module["spec"]["environment"] == {
        "RELEASE_TAG": (
            "production-release/${data.terraform_remote_state.infrastructure.outputs."
            "stacksmith_operation_bridge_app_release_name}"
        )
    }
    assert "depends_on" not in module
    assert infrastructure["output"]["stacksmith_operation_bridge_app_release_name"] == {
        "value": "${module.app.release_name}",
        "sensitive": True,
    }
    assert "app" not in generated["module"]
    assert "provider" not in generated
    assert generated["terraform"]["backend"]["local"]["path"] == (
        "../.state/application/operations/terraform.tfstate"
    )
    assert generated["data"]["terraform_remote_state"]["infrastructure"] == {
        "backend": "local",
        "config": {"path": "../.state/application/terraform.tfstate"},
    }
    assert "variable" not in generated


def test_infrastructure_bridge_outputs_exist_before_operations_are_declared():
    stack = StackDefinition.model_validate(
        {
            "name": "application",
            "components": {"app": {"type": "application"}},
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
            },
            "outputs": {"release_name": {}},
        }
    )

    generated = generate_tf_json(stack, config, {})

    assert generated["output"]["stacksmith_operation_bridge_app_release_name"] == {
        "value": "${module.app.release_name}",
        "sensitive": True,
    }


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

    generated = generate_operations_tf_json(
        stack,
        _config(),
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
        generate_operations_tf_json(
            unknown_dependency,
            _config(),
            operation_names=["deploy"],
        )
    with pytest.raises(
        StacksmithConfigError,
        match="publish -> deploy -> publish",
    ):
        generate_operations_tf_json(
            cyclic_dependency,
            _config(),
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

    write_operations_tf_json(stack, _config(), tmp_path)

    runner_dir = tmp_path / ".stacksmith-operation-runner"
    runner_module = (runner_dir / "main.tf").read_text()
    assert "terraform_data" in runner_module
    assert "nonsensitive(jsonencode(var.spec))" in runner_module
    local_runner = (runner_dir / "local.py").read_text()
    assert "subprocess.run" in local_runner
    assert "subprocess.DEVNULL" in local_runner
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

    write_operations_tf_json(stack, ToolConfig.model_validate(config_data), tmp_path)
    write_operations_tf_json(stack, _config(), tmp_path)

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

    generated = generate_operations_tf_json(
        stack,
        ToolConfig.model_validate(config_data),
        operation_names=["deploy_app"],
    )

    spec = generated["module"]["stacksmith_operation_deploy_app"]["spec"]
    assert spec["poll_interval_seconds"] == 2
    assert spec["timeout_seconds"] == 600


@pytest.mark.parametrize(
    ("stream_output", "expected_stdout", "expected_stderr"),
    (
        (
            True,
            "Stacksmith simple GitOps reconciliation completed: environment=dev "
            "message=Hello from development project=stacksmith\n",
            "Stacksmith simple GitOps reconciliation error stream: environment=dev "
            "project=stacksmith\n",
        ),
        (False, "", ""),
    ),
)
def test_local_operation_runner_controls_spec_environment_output(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    tmp_path: Path,
    stream_output: bool,
    expected_stdout: str,
    expected_stderr: str,
):
    monkeypatch.setenv(
        "STACKSMITH_OPERATION_SPEC",
        json.dumps(
            {
                "runner": "local",
                "command": [
                    "sh",
                    "-c",
                    'echo "Stacksmith simple GitOps reconciliation completed: environment=$STACKSMITH_OPERATION_ENVIRONMENT message=$STACKSMITH_OPERATION_MESSAGE project=$STACKSMITH_OPERATION_PROJECT"; echo "Stacksmith simple GitOps reconciliation error stream: environment=$STACKSMITH_OPERATION_ENVIRONMENT project=$STACKSMITH_OPERATION_PROJECT" 1>&2',
                ],
                "environment": {
                    "STACKSMITH_OPERATION_ENVIRONMENT": "dev",
                    "STACKSMITH_OPERATION_MESSAGE": "Hello from development",
                    "STACKSMITH_OPERATION_PROJECT": "stacksmith",
                },
                "stream_output": stream_output,
                "working_directory": str(tmp_path),
            }
        ),
    )

    runner_path = (
        Path(__file__).parents[1] / "src/stacksmith/assets/operation_runner/local.py"
    )
    runpy.run_path(str(runner_path))

    captured_output = capfd.readouterr()
    assert captured_output.out == expected_stdout
    assert captured_output.err == expected_stderr


def test_local_operation_runner_masks_literals_across_chunk_boundaries(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    tmp_path: Path,
):
    class _Pipe:
        def __init__(self, chunks: list[str]):
            self._chunks = chunks

        def read(self, _size: int) -> str:
            return self._chunks.pop(0) if self._chunks else ""

    class _Popen:
        def __init__(self, *args, **kwargs):
            self.stdout = _Pipe(["prefix super", "-secret suffix\n"])
            self.stderr = _Pipe(["err super-se", "cret again\n"])

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "Popen", _Popen)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("Expected streaming mask path via Popen"),
    )
    monkeypatch.setenv(
        "STACKSMITH_OPERATION_SPEC",
        json.dumps(
            {
                "runner": "local",
                "command": ["sh", "-c", "echo not-used"],
                "environment": {},
                "stream_output": True,
                "working_directory": str(tmp_path),
                "mask_literals": ["super-secret"],
            }
        ),
    )

    runner_path = (
        Path(__file__).parents[1] / "src/stacksmith/assets/operation_runner/local.py"
    )
    runpy.run_path(str(runner_path))

    captured_output = capfd.readouterr()
    assert captured_output.out == "prefix *** suffix\n"
    assert captured_output.err == "err *** again\n"


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
    calls: list[tuple[list[str], Path, dict[str, object]]] = []
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

    def _fake_run_terragrunt(args, working_dir, **kwargs):
        calls.append((args, working_dir, kwargs))
        _write_safe_operation_plan(kwargs)
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
    expected_plan_args = [
        "plan",
        "-parallelism=10",
    ]
    if force_rerun:
        expected_plan_args.append(
            "-replace=module.stacksmith_operation_deploy_app.terraform_data.operation"
        )
    assert calls[0][0] == expected_plan_args
    assert calls[0][2]["no_cas"] is True
    assert calls[1][0][0:2] == ["apply", "-parallelism=10"]
    assert calls[1][0][2].endswith("stacksmith-operation.tfplan")


def test_plan_single_stack_operation_uses_targeted_dry_run(
    monkeypatch,
    tmp_path: Path,
):
    calls = {}
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
        _write_safe_operation_plan(kwargs)
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
        "-parallelism=10",
        "-replace=module.stacksmith_operation_deploy_app.terraform_data.operation",
    ]
    assert "auto_approve" not in calls["run"][2]


def test_plan_stack_operations_selects_all_operations_when_omitted(
    monkeypatch,
    tmp_path: Path,
):
    calls = {}
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
    config = _config()
    config.operations["deploy"].trigger = "manual"
    monkeypatch.setattr(
        api,
        "load_runtime_config",
        lambda *args, **kwargs: (tmp_path, [], config),
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

    def _fake_run(args, working_dir, **kwargs):
        calls["args"] = args
        _write_safe_operation_plan(kwargs)
        return 0

    monkeypatch.setattr(api, "run_terragrunt", _fake_run)

    result = api.plan_stack_operations(stack.source_path)

    assert result == {
        "operations": ["deploy_app"],
        "execution_order": ["deploy_app"],
        "exit_code": 0,
    }
    assert calls["args"] == [
        "plan",
        "-parallelism=10",
    ]


def test_plan_stack_operations_is_no_op_without_after_apply_operations(
    monkeypatch,
    tmp_path: Path,
):
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
    config = _config()
    config.operations["deploy"].trigger = "manual"
    monkeypatch.setattr(
        api,
        "load_runtime_config",
        lambda *args, **kwargs: (tmp_path, [], config),
    )
    monkeypatch.setattr(
        api,
        "_prepare_stack_definition",
        lambda *args, **kwargs: (stack, {}),
    )

    result = api.plan_stack_operations(stack.source_path, after_apply_only=True)

    assert result == {"operations": [], "execution_order": [], "exit_code": 0}


def test_infrastructure_apply_reconciles_after_apply_operations(
    monkeypatch,
    tmp_path: Path,
):
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
    config = _config()
    calls = {}
    monkeypatch.setattr(
        api,
        "load_runtime_config",
        lambda *args, **kwargs: (tmp_path / ".cache", [], config),
    )
    monkeypatch.setattr(
        api,
        "_prepare_stack_definition",
        lambda *args, **kwargs: (stack, {}),
    )
    monkeypatch.setattr(
        api,
        "_generate_single_stack",
        lambda *args, **kwargs: tmp_path / ".stacksmith",
    )
    monkeypatch.setattr(api, "run_terragrunt", lambda *args, **kwargs: 0)

    def _fake_execute(*args, **kwargs):
        calls["operation"] = (args, kwargs)
        return {"exit_code": 0}

    monkeypatch.setattr(api, "_execute_prepared_operations", _fake_execute)

    assert api.run_stack_action("apply", stack.source_path) == 0
    assert calls["operation"][0][0] == api.TerragruntAction.APPLY
    assert calls["operation"][0][3] == ["deploy_app"]


def test_run_stack_operations_uses_one_dependency_aware_apply(
    monkeypatch,
    tmp_path: Path,
):
    calls: dict[str, object] = {"runs": []}
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

    def _fake_generate(stack, config, output_dir, operation_names, **kwargs):
        calls["generated_operations"] = operation_names
        return tmp_path / "build"

    def _fake_run(args, working_dir, **kwargs):
        calls["runs"].append((args, working_dir, kwargs))
        _write_safe_operation_plan(kwargs)
        return 0

    monkeypatch.setattr(api, "_generate_operation_stack", _fake_generate)
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
    assert calls["runs"][0][0] == [
        "plan",
        "-parallelism=3",
        "-replace=module.stacksmith_operation_deploy.terraform_data.operation",
        "-replace=module.stacksmith_operation_docs.terraform_data.operation",
    ]
    assert calls["runs"][1][0][0:2] == ["apply", "-parallelism=3"]
