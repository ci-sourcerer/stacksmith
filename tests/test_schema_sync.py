import json
from importlib.resources import files

import pytest
from pydantic import BaseModel
from stacksmith.models import (
    BackendConfig,
    ComponentDefinition,
    ModuleMapping,
    ModulePropertySpec,
    PlanValidation,
    ProviderConfigSpec,
    ProviderFamily,
    ProviderInstance,
    StackDefinition,
    StackMeta,
    TofuConfig,
    ToolConfig,
    TransformSpec,
    ValidationSpec,
)


def _load_schema(name: str) -> dict[str, object]:
    return json.loads(
        files("stacksmith.schemas").joinpath(name).read_text(encoding="utf-8")
    )


def _field_names(model: type[BaseModel]) -> set[str]:
    return set(model.model_fields)


def test_stack_definition_fields_match_stack_schema():
    schema = _load_schema("stack.schema.json")
    schema_props = set(schema["properties"])

    assert _field_names(StackDefinition) - {"source_path"} == {
        "name",
        "tags",
        "depends_on",
        "mock_outputs",
        "components",
    }
    assert schema_props == {
        "name",
        "tags",
        "depends_on",
        "mock_outputs",
        "components",
    }

    assert _field_names(StackMeta) == {"name"}
    assert schema["properties"]["name"]["type"] == "string"

    assert _field_names(ComponentDefinition) == {"type", "tags", "properties"}
    assert set(
        schema["properties"]["components"]["additionalProperties"]["properties"]
    ) == {
        "type",
        "tags",
        "properties",
    }


def test_tool_config_fields_match_config_schema():
    schema = _load_schema("config.schema.json")
    schema_props = set(schema["properties"])

    assert _field_names(ToolConfig) - {"source_path"} == {
        "backend",
        "tofu",
        "provider_mappings",
        "module_mappings",
        "var_validations",
        "plan_validations",
        "remote_auth",
    }
    assert schema_props == {
        "backend",
        "tofu",
        "provider_mappings",
        "module_mappings",
        "var_validations",
        "plan_validations",
        "remote_auth",
    }

    assert _field_names(BackendConfig) == {"type"}
    backend_schema = schema["properties"]["backend"]
    assert backend_schema["type"] == "object"
    assert set(backend_schema["required"]) == {"type"}
    assert backend_schema["properties"]["type"]["type"] == "string"
    assert backend_schema["additionalProperties"] is True

    assert _field_names(TofuConfig) == {"version"}

    assert _field_names(ProviderFamily) == {"source", "version", "instances"}
    assert _field_names(ProviderConfigSpec) == {"inline", "script", "data"}
    assert _field_names(ProviderInstance) == {"alias", "config"}
    provider_family_ref = schema["properties"]["provider_mappings"][
        "additionalProperties"
    ]
    assert provider_family_ref == {"$ref": "#/$defs/providerFamily"}
    provider_config_spec = schema["$defs"]["providerConfigSpec"]
    assert len(provider_config_spec["oneOf"]) == 3
    assert set(schema["$defs"]["providerFamily"]["properties"]) == {
        "source",
        "version",
        "instances",
    }
    assert set(schema["$defs"]["providerInstance"]["properties"]) == {
        "alias",
        "config",
    }

    assert _field_names(ModuleMapping) == {
        "description",
        "source",
        "version",
        "auto_inject",
        "tags",
        "providers",
        "properties",
    }
    assert set(
        schema["properties"]["module_mappings"]["additionalProperties"]["properties"]
    ) == {
        "description",
        "source",
        "version",
        "auto_inject",
        "tags",
        "providers",
        "properties",
    }

    assert _field_names(ModulePropertySpec) == {
        "mapped_to",
        "default",
        "transform",
        "validation",
        "auto_inject",
    }
    assert set(schema["$defs"]["modulePropertySpec"]["properties"]) == {
        "mapped_to",
        "default",
        "transform",
        "validation",
        "auto_inject",
    }

    assert _field_names(PlanValidation) == {"description", "enabled", "rule"}
    assert set(schema["$defs"]["planValidation"]["properties"]) == {
        "description",
        "enabled",
        "rule",
    }

    assert _field_names(ValidationSpec) == {"inline", "script"}
    assert _field_names(TransformSpec) == {"inline", "script", "jinja"}
    assert schema["$defs"]["transformSpec"]["oneOf"][0]["properties"] == {
        "inline": schema["$defs"]["transformSpec"]["oneOf"][0]["properties"]["inline"]
    }
    assert schema["$defs"]["transformSpec"]["oneOf"][1]["properties"] == {
        "jinja": schema["$defs"]["transformSpec"]["oneOf"][1]["properties"]["jinja"]
    }


@pytest.mark.parametrize(
    "schema_name, model, expected_properties",
    [
        (
            "config.schema.json",
            ToolConfig,
            {
                "backend",
                "tofu",
                "provider_mappings",
                "module_mappings",
                "var_validations",
                "plan_validations",
                "remote_auth",
            },
        ),
        (
            "stack.schema.json",
            StackDefinition,
            {"name", "tags", "depends_on", "mock_outputs", "components"},
        ),
    ],
)
def test_root_model_fields_exist_in_schema(
    schema_name: str, model: type[BaseModel], expected_properties: set[str]
):
    schema = _load_schema(schema_name)
    assert set(schema["properties"]) == expected_properties
    assert _field_names(model) - {"source_path"} == expected_properties
