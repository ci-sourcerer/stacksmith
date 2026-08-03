from pathlib import Path

import pytest

from stacksmith.exceptions import StacksmithConfigError
from stacksmith.generation import generate_tf_json
from stacksmith.loading import load_stack
from stacksmith.models import StackDefinition, ToolConfig
from stacksmith.stack_outputs import build_stack_mock_outputs


def _config() -> ToolConfig:
    return ToolConfig.model_validate(
        {
            "backend": {"type": "local", "path": ".state"},
            "module_mappings": {
                "producer": {
                    "source": {
                        "source": "registry",
                        "data": {
                            "address": "example/producer",
                            "version": "1.0.0",
                        },
                    },
                    "outputs": {
                        "id": {
                            "mapped_from": "underlying_id",
                            "transform": {
                                "jinja": "component/{{ output.value }}",
                            },
                        }
                    },
                }
            },
        }
    )


def _load_stack(path: Path, source: str) -> StackDefinition:
    path.write_text(source, encoding="utf-8")
    return load_stack(
        path,
        template_context={
            "inputs": {},
            "stack": {"name": "exports", "tags": ["example"]},
        },
    )


def test_generates_transformed_root_output_from_public_component_output(
    tmp_path: Path,
):
    stack = _load_stack(
        tmp_path / "stack.yaml",
        "name: exports\n"
        "tags:\n"
        "  - example\n"
        "components:\n"
        "  producer:\n"
        "    type: producer\n"
        "outputs:\n"
        "  public_id:\n"
        "    description: Public stack identifier.\n"
        '    value: "{{ components.producer.id }}"\n'
        "    transform:\n"
        "      description: Add the stack export namespace.\n"
        '      jinja: "stack/{{ output.name }}/{{ output.value }}"\n'
        "    sensitive: true\n"
        "    mock: mock-id\n",
    )

    generated = generate_tf_json(stack, _config(), {})

    assert generated["output"]["public_id"] == {
        "value": ("stack/public_id/component/${module.producer.underlying_id}"),
        "description": "Public stack identifier.",
        "sensitive": True,
    }
    assert stack.outputs["public_id"].transform.jinja == (
        "stack/{{ output.name }}/{{ output.value }}"
    )


def test_stack_output_transform_can_return_structured_value():
    stack = StackDefinition.model_validate(
        {
            "name": "exports",
            "components": {
                "producer": {"type": "producer"},
            },
            "outputs": {
                "producer": {
                    "value": "{{ components.producer.id }}",
                    "transform": {
                        "jinja": (
                            '{"name": "{{ output.name }}", '
                            '"reference": "{{ output.value }}"}'
                        ),
                    },
                }
            },
        }
    )

    assert generate_tf_json(stack, _config(), {})["output"]["producer"]["value"] == {
        "name": "producer",
        "reference": "component/${module.producer.underlying_id}",
    }


def test_stack_output_mock_uses_the_same_transform():
    stack = StackDefinition.model_validate(
        {
            "name": "exports",
            "outputs": {
                "uri": {
                    "value": "real-id",
                    "transform": {
                        "jinja": "stack://{{ output.value }}",
                    },
                    "mock": "mock-id",
                },
                "without_mock": {
                    "value": "real",
                },
            },
        }
    )

    assert build_stack_mock_outputs(stack) == {
        "uri": "stack://mock-id",
    }


def test_omits_root_output_block_when_stack_exports_nothing():
    stack = StackDefinition.model_validate(
        {
            "name": "exports",
            "components": {
                "producer": {"type": "producer"},
            },
        }
    )

    assert "output" not in generate_tf_json(stack, _config(), {})


def test_rejects_stack_output_transform_computation(tmp_path: Path):
    with pytest.raises(
        StacksmithConfigError,
        match="Stack output transform",
    ):
        _load_stack(
            tmp_path / "stack.yaml",
            "name: exports\n"
            "outputs:\n"
            "  public_id:\n"
            "    value: id\n"
            "    transform:\n"
            '      jinja: "{{ output.value | upper }}"\n',
        )


def test_rejects_stack_output_context_outside_transform(tmp_path: Path):
    with pytest.raises(
        StacksmithConfigError,
        match="only be used in stack output Jinja transforms",
    ):
        _load_stack(
            tmp_path / "stack.yaml",
            'name: exports\ndescription: "{{ output.value }}"\n',
        )


@pytest.mark.parametrize("name", ["not-valid", "123invalid"])
def test_stack_output_name_must_be_an_identifier(name: str):
    with pytest.raises(ValueError, match="Stack output names"):
        StackDefinition.model_validate(
            {
                "name": "exports",
                "outputs": {
                    name: {
                        "value": "value",
                    }
                },
            }
        )
