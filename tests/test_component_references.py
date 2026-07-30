from pathlib import Path
from unittest.mock import patch

import pytest
from stacksmith.exceptions import StacksmithConfigError, StacksmithTransformError
from stacksmith.generation import generate_tf_json
from stacksmith.loading import load_stack
from stacksmith.models import StackDefinition, ToolConfig


def _config(
    output_transform: dict | None = None,
    auto_expose_outputs: bool = False,
) -> ToolConfig:
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
                    "auto_expose_outputs": auto_expose_outputs,
                    "outputs": {
                        "id": {
                            "description": "Public producer identifier.",
                            "mapped_from": "underlying_id",
                            **(
                                {"transform": output_transform}
                                if output_transform is not None
                                else {}
                            ),
                        }
                    },
                },
                "consumer": {
                    "source": {
                        "source": "registry",
                        "data": {
                            "address": "example/consumer",
                            "version": "1.0.0",
                        },
                    }
                },
            },
        }
    )


def _load_rendered_stack(path: Path, source: str) -> StackDefinition:
    path.write_text(source, encoding="utf-8")
    return load_stack(
        path,
        template_context={
            "inputs": {},
            "stack": {"name": "references", "tags": []},
        },
    )


def test_binds_public_component_outputs_in_nested_values(tmp_path: Path):
    stack = _load_rendered_stack(
        tmp_path / "stack.yaml",
        "name: references\n"
        "components:\n"
        "  producer:\n"
        "    type: producer\n"
        "  consumer:\n"
        "    type: consumer\n"
        "    properties:\n"
        '      direct: "{{ components.producer.id }}"\n'
        "      nested:\n"
        '        - "prefix-{{ components.producer.id }}"\n',
    )

    generated = generate_tf_json(stack, _config(), {})

    assert generated["module"]["consumer"]["direct"] == (
        "${module.producer.underlying_id}"
    )
    assert generated["module"]["consumer"]["nested"] == [
        "prefix-${module.producer.underlying_id}"
    ]


def test_binds_component_output_jinja_from_programmatic_stack_definition():
    stack = StackDefinition.model_validate(
        {
            "name": "references",
            "components": {
                "producer": {"type": "producer"},
                "consumer": {
                    "type": "consumer",
                    "properties": {"producer_id": "{{ components.producer.id }}"},
                },
            },
        }
    )

    assert (
        generate_tf_json(stack, _config(), {})["module"]["consumer"]["producer_id"]
        == "${module.producer.underlying_id}"
    )


def test_binds_auto_exposed_component_output(tmp_path: Path):
    stack = _load_rendered_stack(
        tmp_path / "stack.yaml",
        "name: references\n"
        "components:\n"
        "  producer:\n"
        "    type: producer\n"
        "  consumer:\n"
        "    type: consumer\n"
        "    properties:\n"
        '      endpoint: "{{ components.producer.endpoint }}"\n',
    )

    with patch(
        "stacksmith.component_references.discover_module_outputs",
        return_value={"endpoint", "underlying_id"},
    ):
        generated = generate_tf_json(
            stack,
            _config(auto_expose_outputs=True),
            {},
        )

    assert generated["module"]["consumer"]["endpoint"] == "${module.producer.endpoint}"


def test_binds_output_discovered_from_local_module(tmp_path: Path):
    module_dir = tmp_path / "producer"
    module_dir.mkdir()
    (module_dir / "outputs.tf").write_text(
        'output "endpoint" {\n  value = "example.test"\n}\n',
        encoding="utf-8",
    )
    config_data = _config(auto_expose_outputs=True).model_dump()
    config_data["module_mappings"]["producer"]["source"] = {
        "source": "local",
        "data": {"path": str(module_dir)},
    }
    stack = StackDefinition.model_validate(
        {
            "name": "references",
            "components": {
                "producer": {"type": "producer"},
                "consumer": {
                    "type": "consumer",
                    "properties": {
                        "endpoint": "{{ components.producer.endpoint }}",
                    },
                },
            },
        }
    )

    generated = generate_tf_json(stack, ToolConfig.model_validate(config_data), {})

    assert generated["module"]["consumer"]["endpoint"] == "${module.producer.endpoint}"


def test_explicit_output_mapping_claims_auto_exposed_module_name(tmp_path: Path):
    stack = _load_rendered_stack(
        tmp_path / "stack.yaml",
        "name: references\n"
        "components:\n"
        "  producer:\n"
        "    type: producer\n"
        "  consumer:\n"
        "    type: consumer\n"
        "    properties:\n"
        '      value: "{{ components.producer.underlying_id }}"\n',
    )

    with (
        patch(
            "stacksmith.component_references.discover_module_outputs",
            return_value={"endpoint", "underlying_id", "unsafe-output"},
        ),
        pytest.raises(
            StacksmithConfigError,
            match=(
                "does not expose output 'underlying_id'.*"
                "Available outputs: endpoint, id"
            ),
        ),
    ):
        generate_tf_json(
            stack,
            _config(auto_expose_outputs=True),
            {},
        )


def test_applies_jinja_output_transform_with_output_context(tmp_path: Path):
    stack = _load_rendered_stack(
        tmp_path / "stack.yaml",
        "name: references\n"
        "components:\n"
        "  producer:\n"
        "    type: producer\n"
        "  consumer:\n"
        "    type: consumer\n"
        "    properties:\n"
        '      producer_arn: "{{ components.producer.id }}"\n',
    )

    generated = generate_tf_json(
        stack,
        _config(
            {
                "jinja": (
                    "arn:example:{{ component.type }}:"
                    "{{ stack.name }}:{{ output.name }}:"
                    "{{ output.module_output }}:{{ output.value }}"
                )
            }
        ),
        {},
    )

    assert generated["module"]["consumer"]["producer_arn"] == (
        "arn:example:producer:references:id:underlying_id:"
        "${module.producer.underlying_id}"
    )


def test_output_transform_can_return_a_structured_value(tmp_path: Path):
    stack = _load_rendered_stack(
        tmp_path / "stack.yaml",
        "name: references\n"
        "components:\n"
        "  producer:\n"
        "    type: producer\n"
        "  consumer:\n"
        "    type: consumer\n"
        "    properties:\n"
        '      producer: "{{ components.producer.id }}"\n',
    )

    generated = generate_tf_json(
        stack,
        _config(
            {
                "inline": (
                    "def transform(value, **context):\n"
                    "    return {\n"
                    "        'component': context['component']['name'],\n"
                    "        'reference': value,\n"
                    "    }\n"
                )
            }
        ),
        {},
    )

    assert generated["module"]["consumer"]["producer"] == {
        "component": "producer",
        "reference": "${module.producer.underlying_id}",
    }


def test_rejects_structured_output_transform_in_string_interpolation(tmp_path: Path):
    stack = _load_rendered_stack(
        tmp_path / "stack.yaml",
        "name: references\n"
        "components:\n"
        "  producer:\n"
        "    type: producer\n"
        "  consumer:\n"
        "    type: consumer\n"
        "    properties:\n"
        '      producer: "prefix-{{ components.producer.id }}"\n',
    )

    with pytest.raises(
        StacksmithTransformError,
        match="must return a string when the output is interpolated",
    ):
        generate_tf_json(
            stack,
            _config(
                {
                    "jinja": (
                        '{"reference": "{{ output.value }}", '
                        '"name": "{{ output.name }}"}'
                    )
                }
            ),
            {},
        )


def test_output_transform_failure_identifies_component_and_output(tmp_path: Path):
    stack = _load_rendered_stack(
        tmp_path / "stack.yaml",
        "name: references\n"
        "components:\n"
        "  producer:\n"
        "    type: producer\n"
        "  consumer:\n"
        "    type: consumer\n"
        "    properties:\n"
        '      producer: "{{ components.producer.id }}"\n',
    )

    with pytest.raises(
        StacksmithTransformError,
        match="Component 'producer' output 'id' transform",
    ):
        generate_tf_json(
            stack,
            _config(
                {
                    "inline": (
                        "def transform(value, **context):\n"
                        "    raise ValueError('failed intentionally')\n"
                    )
                }
            ),
            {},
        )


def test_rejects_unknown_component_output(tmp_path: Path):
    stack = _load_rendered_stack(
        tmp_path / "stack.yaml",
        "name: references\n"
        "components:\n"
        "  producer:\n"
        "    type: producer\n"
        "  consumer:\n"
        "    type: consumer\n"
        "    properties:\n"
        '      value: "{{ components.producer.missing }}"\n',
    )

    with (
        patch(
            "stacksmith.component_references.discover_module_outputs"
        ) as discover_outputs,
        pytest.raises(
            StacksmithConfigError,
            match="does not expose output 'missing'.*Available outputs: id",
        ),
    ):
        generate_tf_json(stack, _config(), {})

    discover_outputs.assert_not_called()


def test_rejects_unknown_component(tmp_path: Path):
    stack = _load_rendered_stack(
        tmp_path / "stack.yaml",
        "name: references\n"
        "components:\n"
        "  consumer:\n"
        "    type: consumer\n"
        "    properties:\n"
        '      value: "{{ components.missing.id }}"\n',
    )

    with pytest.raises(
        StacksmithConfigError,
        match="unknown component 'missing'",
    ):
        generate_tf_json(stack, _config(), {})


def test_rejects_component_self_reference(tmp_path: Path):
    stack = _load_rendered_stack(
        tmp_path / "stack.yaml",
        "name: references\n"
        "components:\n"
        "  producer:\n"
        "    type: producer\n"
        "    properties:\n"
        '      value: "{{ components.producer.id }}"\n',
    )

    with pytest.raises(
        StacksmithConfigError,
        match="cannot reference its own output",
    ):
        generate_tf_json(stack, _config(), {})


@pytest.mark.parametrize(
    "expression",
    [
        "{% if components.producer.id %}true{% endif %}",
        "{{ components.producer.id | upper }}",
    ],
)
def test_rejects_component_outputs_in_jinja_computation(
    tmp_path: Path, expression: str
):
    with pytest.raises(
        StacksmithConfigError,
        match="Component output",
    ):
        _load_rendered_stack(
            tmp_path / "stack.yaml",
            "name: references\n"
            f"description: '{expression}'\n"
            "components:\n"
            "  producer:\n"
            "    type: producer\n",
        )


def test_rejects_component_outputs_outside_runtime_values(tmp_path: Path):
    with pytest.raises(
        StacksmithConfigError,
        match="only be referenced from component properties, stack outputs, or operation inputs",
    ):
        _load_rendered_stack(
            tmp_path / "stack.yaml",
            "name: references\n"
            'description: "{{ components.producer.id }}"\n'
            "components:\n"
            "  producer:\n"
            "    type: producer\n",
        )


def test_rejects_raw_module_references(tmp_path: Path):
    with pytest.raises(
        StacksmithConfigError,
        match="Raw Terraform module references are not supported",
    ):
        _load_rendered_stack(
            tmp_path / "stack.yaml",
            "name: references\n"
            "components:\n"
            "  producer:\n"
            "    type: producer\n"
            "  consumer:\n"
            "    type: consumer\n"
            "    properties:\n"
            '      value: "${module.producer.underlying_id}"\n',
        )
