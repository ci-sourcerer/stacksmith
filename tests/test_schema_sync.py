import json
from importlib.resources import files

import pytest
from pydantic import BaseModel
from stacksmith.models import (
    BackendConfig,
    ComponentDefinition,
    DefaultModuleMapping,
    ModuleMapping,
    ModulePropertySpec,
    PlanValidation,
    ProviderConfigSpec,
    ProviderFamily,
    ProviderInstance,
    RunFile,
    StackDefinition,
    StackMeta,
    ToolBinaryConfig,
    ToolConfig,
    ToolsConfig,
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
        "operations",
    }
    assert schema_props == {
        "name",
        "tags",
        "depends_on",
        "mock_outputs",
        "components",
        "operations",
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
        "tools",
        "provider_mappings",
        "module_mappings",
        "default_module_mapping",
        "operations",
        "var_validations",
        "plan_validations",
        "remote_auth",
    }
    assert schema_props == {
        "backend",
        "tools",
        "provider_mappings",
        "module_mappings",
        "default_module_mapping",
        "operations",
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

    assert _field_names(ToolsConfig) == {"tofu", "terragrunt"}
    assert _field_names(ToolBinaryConfig) == {"version", "download"}

    assert _field_names(ProviderFamily) == {"source", "instances"}
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
        "instances",
    }
    assert set(schema["$defs"]["providerInstance"]["properties"]) == {
        "alias",
        "config",
    }

    assert _field_names(ModuleMapping) == {
        "description",
        "source",
        "auto_inject",
        "tags",
        "providers",
        "properties",
    }
    assert _field_names(DefaultModuleMapping) == {
        "source",
        "auto_inject",
        "tags",
        "providers",
        "properties",
    }
    assert set(schema["$defs"]["defaultModuleMapping"]["properties"]) == {
        "source",
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


def test_runfile_fields_match_runfile_schema():
    schema = _load_schema("runfile.schema.json")

    assert _field_names(RunFile) == {
        "merge_mode",
        "merge_rules",
        "stacks",
        "configs",
        "vars",
    }
    assert set(schema["properties"]) == {
        "merge_mode",
        "merge_rules",
        "stacks",
        "configs",
        "vars",
    }


@pytest.mark.parametrize(
    "schema_name, model, expected_properties",
    [
        (
            "config.schema.json",
            ToolConfig,
            {
                "backend",
                "tools",
                "provider_mappings",
                "module_mappings",
                "default_module_mapping",
                "operations",
                "var_validations",
                "plan_validations",
                "remote_auth",
            },
        ),
        (
            "stack.schema.json",
            StackDefinition,
            {
                "name",
                "tags",
                "depends_on",
                "mock_outputs",
                "components",
                "operations",
            },
        ),
        (
            "runfile.schema.json",
            RunFile,
            {"merge_mode", "merge_rules", "stacks", "configs", "vars"},
        ),
    ],
)
def test_root_model_fields_exist_in_schema(
    schema_name: str, model: type[BaseModel], expected_properties: set[str]
):
    schema = _load_schema(schema_name)
    assert set(schema["properties"]) == expected_properties
    assert _field_names(model) - {"source_path"} == expected_properties
