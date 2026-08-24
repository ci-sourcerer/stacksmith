import json
from importlib.resources import files
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from stacksmith.loading.validation import load_fragment_schema
from stacksmith.models import (
    BackendConfig,
    BackendSpec,
    ComponentDefinition,
    ComponentPropertyExpectation,
    ComponentPropertyTestCase,
    DefaultModuleMapping,
    FixtureSpec,
    JenkinsOperationDefinition,
    LocalOperationDefinition,
    MergeRule,
    ModuleInputSet,
    ModuleInputSpec,
    ModuleMapping,
    ModuleOutputSpec,
    ModulePropertySpec,
    OperationInputSpec,
    OperationInvocation,
    PlanPolicyTestCase,
    PlanTestResource,
    PlanValidation,
    ProviderConfigSpec,
    ProviderFamily,
    ProviderInstance,
    RunFile,
    StackDefinition,
    StackMeta,
    StackOutputDefinition,
    StackOutputTransformSpec,
    StacksmithTestFixtures,
    StacksmithTestManifest,
    ToolBinaryConfig,
    ToolConfig,
    ToolsConfig,
    TransformSpec,
    ValidationSpec,
    VariablePolicyTestCase,
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

    assert set(schema["required"]) == {"name"}
    assert _field_names(StackDefinition) - {"source_path"} == {
        "name",
        "description",
        "tags",
        "depends_on",
        "components",
        "outputs",
        "operations",
    }
    assert schema_props == {
        "name",
        "description",
        "tags",
        "depends_on",
        "components",
        "outputs",
        "operations",
    }

    assert _field_names(StackMeta) == {"name", "description"}
    assert schema["properties"]["name"]["type"] == "string"

    assert _field_names(ComponentDefinition) == {
        "type",
        "description",
        "tags",
        "properties",
    }
    assert set(
        schema["properties"]["components"]["additionalProperties"]["properties"]
    ) == {
        "type",
        "description",
        "tags",
        "properties",
    }
    assert _field_names(OperationInvocation) == {
        "use",
        "description",
        "with_",
        "rerun_token",
        "depends_on",
    }
    assert set(
        schema["properties"]["operations"]["additionalProperties"]["properties"]
    ) == {
        "use",
        "description",
        "with",
        "rerun_token",
        "depends_on",
    }
    assert _field_names(StackOutputDefinition) == {
        "description",
        "value",
        "transform",
        "sensitive",
        "mock",
    }
    assert set(
        schema["properties"]["outputs"]["additionalProperties"]["properties"]
    ) == {
        "description",
        "value",
        "transform",
        "sensitive",
        "mock",
    }
    assert _field_names(StackOutputTransformSpec) == {
        "description",
        "jinja",
    }


def test_tool_config_fields_match_config_schema():
    schema = _load_schema("config.schema.json")
    schema_props = set(schema["properties"])

    assert set(schema["required"]) == {"backend"}
    assert _field_names(ToolConfig) - {"source_path"} == {
        "description",
        "backend",
        "tools",
        "provider_mappings",
        "module_input_sets",
        "required_module_input_sets",
        "module_mappings",
        "default_module_mapping",
        "operations",
        "var_validations",
        "plan_validations",
        "remote_auth",
    }
    assert schema_props == {
        "description",
        "backend",
        "tools",
        "provider_mappings",
        "module_input_sets",
        "required_module_input_sets",
        "module_mappings",
        "default_module_mapping",
        "operations",
        "var_validations",
        "plan_validations",
        "remote_auth",
    }

    assert _field_names(BackendConfig) == {"type"}
    assert _field_names(BackendSpec) == {"inline", "script", "data"}
    backend_schema = schema["properties"]["backend"]
    assert backend_schema["$ref"] == "#/$defs/backendSpec"
    backend_spec_schema = schema["$defs"]["backendSpec"]
    assert len(backend_spec_schema["oneOf"]) == 3
    backend_config_schema = schema["$defs"]["backendConfig"]
    assert set(backend_config_schema["required"]) == {"type"}

    assert _field_names(ToolsConfig) == {"tofu", "terragrunt"}
    assert _field_names(ToolBinaryConfig) == {"version", "download"}

    assert _field_names(ProviderFamily) == {"description", "source", "instances"}
    assert _field_names(ProviderConfigSpec) == {"inline", "script", "data"}
    assert _field_names(ProviderInstance) == {"description", "alias", "config"}
    provider_family_ref = schema["properties"]["provider_mappings"][
        "additionalProperties"
    ]
    assert provider_family_ref == {"$ref": "#/$defs/providerFamily"}
    provider_config_spec = schema["$defs"]["providerConfigSpec"]
    assert len(provider_config_spec["oneOf"]) == 3
    assert set(schema["$defs"]["providerFamily"]["properties"]) == {
        "description",
        "source",
        "instances",
    }
    assert set(schema["$defs"]["providerInstance"]["properties"]) == {
        "description",
        "alias",
        "config",
    }

    assert _field_names(ModuleMapping) == {
        "description",
        "source",
        "auto_inject_inputs",
        "required_input_sets",
        "auto_expose_outputs",
        "tags",
        "providers",
        "properties",
        "outputs",
    }
    assert _field_names(DefaultModuleMapping) == {
        "description",
        "source",
        "auto_inject_inputs",
        "required_input_sets",
        "auto_expose_outputs",
        "tags",
        "providers",
        "properties",
        "outputs",
    }
    assert set(schema["$defs"]["defaultModuleMapping"]["properties"]) == {
        "description",
        "source",
        "auto_inject_inputs",
        "required_input_sets",
        "auto_expose_outputs",
        "tags",
        "providers",
        "properties",
        "outputs",
    }
    assert set(
        schema["properties"]["module_mappings"]["additionalProperties"]["properties"]
    ) == {
        "description",
        "source",
        "auto_inject_inputs",
        "required_input_sets",
        "auto_expose_outputs",
        "tags",
        "providers",
        "properties",
        "outputs",
    }

    assert _field_names(ModulePropertySpec) == {
        "description",
        "mapped_to",
        "default",
        "transform",
        "validation",
        "auto_inject_inputs",
    }
    assert _field_names(ModuleInputSet) == {
        "description",
        "inputs",
    }
    assert set(schema["$defs"]["moduleInputSet"]["properties"]) == {
        "description",
        "inputs",
    }
    assert _field_names(ModuleInputSpec) == {
        "type",
        "description",
        "sensitive",
        "nullable",
        "default",
    }
    assert set(schema["$defs"]["moduleInputSpec"]["properties"]) == {
        "type",
        "description",
        "sensitive",
        "nullable",
        "default",
    }
    assert set(schema["$defs"]["modulePropertySpec"]["properties"]) == {
        "description",
        "mapped_to",
        "default",
        "transform",
        "validation",
        "auto_inject_inputs",
    }
    assert _field_names(ModuleOutputSpec) == {
        "description",
        "mapped_from",
        "transform",
    }
    assert set(schema["$defs"]["moduleOutputSpec"]["properties"]) == {
        "description",
        "mapped_from",
        "transform",
    }
    assert _field_names(OperationInputSpec) == {
        "description",
        "required",
        "secret",
    }
    assert set(schema["$defs"]["operationInputSpec"]["properties"]) == {
        "description",
        "required",
        "secret",
    }
    operation_definitions = schema["$defs"]["operationDefinition"]["oneOf"]
    assert _field_names(LocalOperationDefinition) == set(
        operation_definitions[0]["properties"]
    )
    assert _field_names(JenkinsOperationDefinition) == set(
        operation_definitions[1]["properties"]
    )

    assert _field_names(PlanValidation) == {"description", "enabled", "rule"}
    assert set(schema["$defs"]["planValidation"]["properties"]) == {
        "description",
        "enabled",
        "rule",
    }

    assert _field_names(ValidationSpec) == {"description", "inline", "script"}
    assert _field_names(TransformSpec) == {
        "description",
        "inline",
        "script",
        "jinja",
    }
    assert schema["$defs"]["transformSpec"]["oneOf"][0]["properties"] == {
        "description": schema["$defs"]["transformSpec"]["oneOf"][0]["properties"][
            "description"
        ],
        "inline": schema["$defs"]["transformSpec"]["oneOf"][0]["properties"]["inline"],
    }
    assert schema["$defs"]["transformSpec"]["oneOf"][1]["properties"] == {
        "description": schema["$defs"]["transformSpec"]["oneOf"][1]["properties"][
            "description"
        ],
        "jinja": schema["$defs"]["transformSpec"]["oneOf"][1]["properties"]["jinja"],
    }


def test_runfile_fields_match_runfile_schema():
    schema = _load_schema("runfile.schema.json")

    assert _field_names(RunFile) == {
        "description",
        "merge_mode",
        "merge_rules",
        "stacks",
        "configs",
        "vars",
    }
    assert set(schema["properties"]) == {
        "description",
        "merge_mode",
        "merge_rules",
        "stacks",
        "configs",
        "vars",
    }
    assert _field_names(MergeRule) == {"description", "select", "mode"}
    assert set(schema["$defs"]["mergeRule"]["properties"]) == {
        "description",
        "select",
        "mode",
    }


def test_test_manifest_fields_match_schema():
    schema = _load_schema("test_manifest.schema.json")

    assert _field_names(StacksmithTestManifest) - {"source_path"} == {
        "description",
        "fixtures",
        "var_validations",
        "plan_validations",
        "component_properties",
    }
    assert set(schema["properties"]) == {
        "description",
        "fixtures",
        "var_validations",
        "plan_validations",
        "component_properties",
    }

    assert _field_names(FixtureSpec) == {"inline", "script"}
    assert _field_names(StacksmithTestFixtures) == {"mode", "setup", "teardown"}
    assert _field_names(VariablePolicyTestCase) == {"name", "value", "expect"}
    assert _field_names(PlanPolicyTestCase) == {
        "name",
        "plan",
        "resources",
        "context",
        "expect",
    }
    assert _field_names(PlanTestResource) == {
        "type",
        "address",
        "actions",
        "change",
        "before",
        "after",
        "after_unknown",
    }
    assert _field_names(ComponentPropertyExpectation) == {"value", "output_name"}
    assert _field_names(ComponentPropertyTestCase) == {
        "name",
        "value",
        "inputs",
        "expect",
    }


def test_vars_schema_accepts_free_form_mappings():
    schema = _load_schema("vars.schema.json")

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is True


@pytest.mark.parametrize(
    "effective_schema_name",
    [
        "config.schema.json",
        "runfile.schema.json",
        "stack.schema.json",
        "test_manifest.schema.json",
        "vars.schema.json",
    ],
)
def test_generated_layer_schema_matches_runtime_fragment_schema(
    effective_schema_name: str,
):
    assert _load_schema(
        effective_schema_name.replace(".schema.json", ".layer.schema.json")
    ) == load_fragment_schema(effective_schema_name)


@pytest.mark.parametrize(
    "schema_name, document_paths",
    [
        (
            "config.layer.schema.json",
            [
                Path("examples/shared-config-repo/stacksmith-base-config.yaml"),
                Path("examples/shared-config-repo/stacksmith-config.yaml"),
            ],
        ),
        (
            "runfile.layer.schema.json",
            [
                Path("examples/gitops-repo/common/stacksmith.yaml"),
                Path("examples/gitops-repo/environments/dev.yaml"),
            ],
        ),
        (
            "stack.layer.schema.json",
            [
                Path("examples/gitops-repo/manifests/common/platform.stack.yaml"),
                Path("examples/gitops-repo/manifests/common/service.stack.yaml"),
            ],
        ),
        (
            "test_manifest.layer.schema.json",
            [Path("examples/shared-config-repo/tests.yaml")],
        ),
        (
            "vars.layer.schema.json",
            [Path("examples/gitops-repo/vars/vars.dev.yaml")],
        ),
    ],
)
def test_example_layers_match_editor_schemas(
    schema_name: str, document_paths: list[Path]
):
    validator = Draft202012Validator(_load_schema(schema_name))

    for document_path in document_paths:
        assert not (
            errors := list(
                validator.iter_errors(
                    yaml.safe_load(document_path.read_text(encoding="utf-8"))
                )
            )
        ), f"{document_path}: {[error.message for error in errors]}"


def test_vscode_associates_mergeable_documents_with_layer_schemas():
    settings = json.loads(Path(".vscode/settings.json").read_text(encoding="utf-8"))

    assert all(
        schema_name.endswith(".layer.schema.json")
        for schema_name in settings["yaml.schemas"]
    )
    assert all(
        schema["url"].endswith(".layer.schema.json")
        for schema in settings["json.schemas"]
    )


@pytest.mark.parametrize(
    "schema_name, model, expected_properties",
    [
        (
            "config.schema.json",
            ToolConfig,
            {
                "description",
                "backend",
                "tools",
                "provider_mappings",
                "module_input_sets",
                "required_module_input_sets",
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
                "description",
                "tags",
                "depends_on",
                "components",
                "outputs",
                "operations",
            },
        ),
        (
            "runfile.schema.json",
            RunFile,
            {
                "description",
                "merge_mode",
                "merge_rules",
                "stacks",
                "configs",
                "vars",
            },
        ),
        (
            "test_manifest.schema.json",
            StacksmithTestManifest,
            {
                "description",
                "fixtures",
                "var_validations",
                "plan_validations",
                "component_properties",
            },
        ),
    ],
)
def test_root_model_fields_exist_in_schema(
    schema_name: str, model: type[BaseModel], expected_properties: set[str]
):
    schema = _load_schema(schema_name)
    assert set(schema["properties"]) == expected_properties
    assert _field_names(model) - {"source_path"} == expected_properties
