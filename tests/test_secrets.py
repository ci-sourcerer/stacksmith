import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from jinja2 import TemplateRuntimeError

from stacksmith.exceptions import StacksmithConfigError
from stacksmith.generation import generate_operations_tf_json, generate_tf_json
from stacksmith.generation.terragrunt import generate_terragrunt_json
from stacksmith.loading import load_stack
from stacksmith.models import InputSpec, StackDefinition, ToolConfig
from stacksmith.provenance import redact_values
from stacksmith.secrets import (
    SECRET_SOURCES_LOCAL,
    normalize_secrets,
    read_secret,
    secret_environment,
    secret_fingerprint,
)
from stacksmith.templating import (
    create_sandboxed_jinja_environment,
    render_jinja_template_values,
)
from stacksmith.variables import resolve_inputs


def _config():
    return ToolConfig.model_validate(
        {
            "backend": {"data": {"type": "local", "path": ".state"}},
            "default_module_mapping": {
                "source": {
                    "source": "registry",
                    "data": {"address": "example/module", "version": "1.0.0"},
                }
            },
            "operations": {
                "deploy": {
                    "runner": "local",
                    "command": [
                        sys.executable,
                        "-c",
                        "import os; print(os.environ['TOKEN'])",
                    ],
                    "environment": {"mode": "auto"},
                    "stream_output": True,
                    "inputs": {"token": {"secret": True, "required": True}},
                }
            },
        }
    )


def _operation_stack(reference):
    return StackDefinition.model_validate(
        {
            "name": "test",
            "operations": {"deploy": {"use": "deploy", "with": {"token": reference}}},
        }
    )


@pytest.mark.parametrize(
    "value", ["", "false", "123", "{{ inputs.other }}", "line one\nline two\n"]
)
def test_environment_secrets_preserve_text(monkeypatch, value):
    monkeypatch.setenv("TEST_SECRET", value)
    assert read_secret({"$secret": {"env": "TEST_SECRET"}}) == value


def test_file_secrets_preserve_whitespace_and_resolve_against_declaration(tmp_path):
    (tmp_path / "token").write_bytes(b"  value\r\n")
    assert (
        read_secret(normalize_secrets({"$secret": {"file": "token"}}, tmp_path))
        == "  value\r\n"
    )
    assert (
        read_secret(
            normalize_secrets(
                {"$secret": {"file": "token", "strip_trailing_newline": True}}, tmp_path
            )
        )
        == "  value"
    )


@pytest.mark.parametrize(
    "source",
    [
        {},
        {"env": "X", "file": "/x"},
        {"env": 2},
        {"env": "X", "extra": True},
        {"env": "X", "strip_trailing_newline": True},
        {"file": "relative"},
    ],
)
def test_rejects_invalid_secret_sources(source):
    with pytest.raises(StacksmithConfigError):
        normalize_secrets({"$secret": source})


def test_unreadable_sources_do_not_disclose_contents(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_SECRET", raising=False)
    with pytest.raises(StacksmithConfigError, match="not set"):
        read_secret({"$secret": {"env": "TEST_SECRET"}})
    (tmp_path / "invalid").write_bytes(b"SENTINEL\xff")
    with pytest.raises(StacksmithConfigError) as error:
        read_secret({"$secret": {"file": str(tmp_path / "invalid")}})
    assert "SENTINEL" not in str(error.value)


def test_input_layer_replaces_entire_secret_reference(tmp_path):
    (tmp_path / "vars.yaml").write_text("token:\n  $secret:\n    file: old\n")
    assert resolve_inputs(
        vars_file=tmp_path / "vars.yaml",
        input_layers=[("var", 'token={"$secret":{"env":"TOKEN"}}')],
    )["token"] == {"$secret": {"env": "TOKEN"}}


def test_metadata_treats_prefixed_environment_as_a_source(monkeypatch):
    monkeypatch.setenv("STACKSMITH_VAR_TOKEN", "SENTINEL")
    assert resolve_inputs(input_specs={"token": InputSpec(secret=True)})["token"] == {
        "$secret": {"env": "STACKSMITH_VAR_TOKEN"}
    }
    with pytest.raises(StacksmithConfigError, match="requires an env or file"):
        resolve_inputs(
            input_specs={"token": InputSpec(secret=True)},
            input_layers=[("var", "token=SENTINEL")],
        )


@pytest.mark.parametrize(
    "template",
    [
        "{{ inputs.token | upper }}",
        "{% if inputs.token %}yes{% endif %}",
        '{{ inputs.token ~ "x" }}',
        "{{ inputs.token.env }}",
        "{{ inputs }}",
    ],
)
def test_secret_template_logic_is_rejected(template):
    with pytest.raises(TemplateRuntimeError):
        render_jinja_template_values(
            template,
            {"inputs": {"token": {"$secret": {"env": "TOKEN"}}}},
            create_sandboxed_jinja_environment(),
        )


def test_direct_interpolation_is_deferred_and_cannot_be_concatenated():
    context = {"inputs": {"token": {"$secret": {"env": "TOKEN"}}}}
    assert (
        render_jinja_template_values(
            "{{ inputs.token }}", context, create_sandboxed_jinja_environment()
        )
        == context["inputs"]["token"]
    )
    with pytest.raises(StacksmithConfigError, match="entire value"):
        render_jinja_template_values(
            "prefix{{ inputs.token }}", context, create_sandboxed_jinja_environment()
        )


def test_stack_rendering_and_generation_never_serialize_contents(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN", 'SENTINEL"\n${not_terraform}')
    (tmp_path / "stack.yaml").write_text(
        'name: test\ncomponents:\n  app:\n    type: app\n    properties:\n      token: "{{ inputs.token }}"\noutputs:\n  token:\n    value: "{{ inputs.token }}"\n'
    )
    inputs = {"token": {"$secret": {"env": "TOKEN"}}}
    stack = load_stack(tmp_path / "stack.yaml", template_context={"inputs": inputs})
    generated = generate_tf_json(stack, _config(), inputs)
    name = next(iter(generated["locals"][SECRET_SOURCES_LOCAL]))
    assert generated["module"]["app"]["token"] == f"${{var.{name}}}"
    assert generated["variable"][name]["sensitive"] is True
    assert generated["output"]["token"]["sensitive"] is True
    assert secret_environment(generated) == {f"TF_VAR_{name}": os.environ["TOKEN"]}
    assert "SENTINEL" not in json.dumps(generated)
    assert generate_terragrunt_json(stack, _config(), inputs)["inputs"] == {}
    assert redact_values(inputs, show_values=True) == {"token": "[REDACTED]"}


def test_secret_cannot_define_stack_structure(tmp_path):
    (tmp_path / "stack.yaml").write_text('name: "{{ inputs.token }}"\n')
    with pytest.raises(StacksmithConfigError, match="only supported"):
        load_stack(
            tmp_path / "stack.yaml",
            template_context={"inputs": {"token": {"$secret": {"env": "TOKEN"}}}},
        )


def test_operation_identity_tracks_secret_rotation_and_rejects_stale_binding(
    monkeypatch,
):
    monkeypatch.setenv("TOKEN", "first-secret")
    monkeypatch.setenv("STACKSMITH_SECRET_FINGERPRINT_KEY", "stable-test-key")
    stack = _operation_stack({"$secret": {"env": "TOKEN"}})
    first = generate_operations_tf_json(stack, _config())
    assert "first-secret" not in json.dumps(first)
    assert "first-secret" in secret_environment(first).values()
    monkeypatch.setenv("TOKEN", "second-secret")
    second = generate_operations_tf_json(stack, _config())
    assert (
        first["module"]["stacksmith_operation_deploy"]["spec"]["identity"]
        != second["module"]["stacksmith_operation_deploy"]["spec"]["identity"]
    )
    with pytest.raises(StacksmithConfigError, match="changed after generation"):
        secret_environment(first)


def test_operation_runner_masks_automatically(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN", "SENTINEL")
    monkeypatch.setenv("STACKSMITH_SECRET_FINGERPRINT_KEY", "stable-test-key")
    document = generate_operations_tf_json(
        _operation_stack({"$secret": {"env": "TOKEN"}}), _config()
    )
    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).parents[1]
                / "src/stacksmith/assets/operation_runner/local.py"
            ),
        ],
        env={
            **os.environ,
            **secret_environment(document),
            "STACKSMITH_OPERATION_SPEC": json.dumps(
                document["module"]["stacksmith_operation_deploy"]["spec"]
            ),
        },
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    assert "SENTINEL" not in result.stdout + result.stderr
    assert "***" in result.stdout


def test_operation_secret_requires_reference_and_fingerprint_key(monkeypatch):
    with pytest.raises(StacksmithConfigError, match="requires an env or file"):
        generate_operations_tf_json(_operation_stack("SENTINEL"), _config())
    monkeypatch.delenv("STACKSMITH_SECRET_FINGERPRINT_KEY", raising=False)
    with pytest.raises(
        StacksmithConfigError, match="STACKSMITH_SECRET_FINGERPRINT_KEY"
    ):
        secret_fingerprint("SENTINEL")


@pytest.mark.parametrize("runner", ["local", "jenkins"])
def test_runner_rejects_changed_values_even_with_a_saved_spec(monkeypatch, runner):
    monkeypatch.setenv("TOKEN", "original-secret")
    monkeypatch.setenv("STACKSMITH_SECRET_FINGERPRINT_KEY", "stable-test-key")
    document = generate_operations_tf_json(
        _operation_stack({"$secret": {"env": "TOKEN"}}), _config()
    )
    spec = document["module"]["stacksmith_operation_deploy"]["spec"]
    environment = {**os.environ, **secret_environment(document)}
    environment[spec["secret_bindings"]["token"]["environment"]] = "changed-secret"
    environment["STACKSMITH_OPERATION_SPEC"] = json.dumps(spec)
    result = subprocess.run(
        [
            sys.executable,
            str(
                Path(__file__).parents[1]
                / f"src/stacksmith/assets/operation_runner/{runner}.py"
            ),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    assert result.returncode != 0
    assert "Operation secret changed after planning" in result.stderr
    assert "original-secret" not in result.stdout + result.stderr
    assert "changed-secret" not in result.stdout + result.stderr


def test_runtime_bindings_ignore_ordinary_module_spec_properties():
    assert (
        secret_environment(
            {"module": {"app": {"source": "example/module", "spec": "plain value"}}}
        )
        == {}
    )


def test_authored_runtime_placeholders_are_rejected():
    with pytest.raises(StacksmithConfigError, match="reserved"):
        normalize_secrets({"$secret_env": "STACKSMITH_SECRET_FINGERPRINT_KEY"})


def test_secret_property_hooks_are_rejected(monkeypatch):
    from stacksmith.generation.properties import apply_property_spec
    from stacksmith.models import ModulePropertySpec

    with pytest.raises(StacksmithConfigError, match="cannot use transforms"):
        apply_property_spec(
            {"$secret": {"env": "TOKEN"}},
            ModulePropertySpec(transform={"jinja": "{{ property.value }}"}),
            {},
            _config(),
        )


def test_secret_input_validation_hooks_are_rejected():
    from stacksmith.models import ValidationSpec

    with pytest.raises(StacksmithConfigError, match="cannot use Python validation"):
        resolve_inputs(
            input_layers=[("var", 'token={"$secret":{"env":"TOKEN"}}')],
            config_validations={
                "token": ValidationSpec(
                    inline="def validate(value, **context): return True"
                )
            },
        )
