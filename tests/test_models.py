import pytest
from stacksmith.models import ModuleMapping, ProviderConfigSpec, ToolConfig


def _base_tool_config_payload() -> dict:
    return {
        "backend": {
            "type": "local",
            "path": ".state",
        },
        "tofu": {
            "version": "1.11.6",
        },
        "provider_mappings": {
            "aws": {
                "source": "hashicorp/aws",
                "version": "6.39.0",
                "instances": {
                    "default": {
                        "config": {
                            "data": {
                                "region": "us-east-1",
                            },
                        }
                    }
                },
            }
        },
        "module_mappings": {
            "aws_s3_bucket": {
                "source": "https://github.com/org/terraform-aws-s3.git",
                "version": "1.0.0",
            }
        },
    }


class TestModuleSourceValidation:
    def test_rejects_file_protocol(self):
        with pytest.raises(ValueError, match="not a local path"):
            ModuleMapping(source="file:///tmp/module", version="1.0.0")

    def test_rejects_relative_dot_slash(self):
        with pytest.raises(ValueError, match="not a local path"):
            ModuleMapping(source="./local-module", version="1.0.0")

    def test_rejects_relative_dotdot_slash(self):
        with pytest.raises(ValueError, match="not a local path"):
            ModuleMapping(source="../parent-module", version="1.0.0")

    def test_rejects_absolute_path(self):
        with pytest.raises(ValueError, match="not an absolute local path"):
            ModuleMapping(source="/opt/modules/my-mod", version="1.0.0")

    def test_allows_git_url(self):
        m = ModuleMapping(
            source="https://github.com/org/terraform-aws-s3.git", version="1.0.0"
        )
        assert m.source == "https://github.com/org/terraform-aws-s3.git"

    def test_allows_registry_address(self):
        m = ModuleMapping(source="hashicorp/aws", version="5.12.0")
        assert m.source == "hashicorp/aws"

    def test_allows_double_slash_subdir(self):
        m = ModuleMapping(
            source="git::https://github.com/org/modules.git//vpc", version="1.0.0"
        )
        assert "//vpc" in m.source


class TestProviderValidation:
    def test_provider_config_spec_requires_one_source(self):
        with pytest.raises(
            ValueError,
            match="Exactly one of 'inline', 'script', or 'data'",
        ):
            ProviderConfigSpec()

    def test_provider_config_spec_accepts_inline(self):
        spec = ProviderConfigSpec(
            inline="def config(**context): return {'region': 'us-east-1'}"
        )
        assert spec.inline is not None

    def test_provider_config_spec_accepts_script(self):
        spec = ProviderConfigSpec(script="scripts/provider_config.py")
        assert spec.script == "scripts/provider_config.py"

    def test_provider_config_spec_accepts_data(self):
        spec = ProviderConfigSpec(data={"region": "us-east-1"})
        assert spec.data == {"region": "us-east-1"}

    def test_provider_family_may_omit_default_instance(self):
        payload = _base_tool_config_payload()
        payload["provider_mappings"]["aws"]["instances"] = {
            "secondary": {
                "alias": "secondary",
                "config": {"data": {"region": "us-west-2"}},
            }
        }

        config = ToolConfig.model_validate(payload)

        assert "default" not in config.provider_mappings["aws"].instances
        assert (
            config.provider_mappings["aws"].instances["secondary"].alias == "secondary"
        )

    def test_provider_instance_config_must_not_be_empty(self):
        payload = _base_tool_config_payload()
        payload["provider_mappings"]["aws"]["instances"]["default"] = {"config": {}}

        with pytest.raises(
            ValueError,
            match="Exactly one of 'inline', 'script', or 'data'",
        ):
            ToolConfig.model_validate(payload)

    def test_provider_instance_data_must_not_be_empty(self):
        payload = _base_tool_config_payload()
        payload["provider_mappings"]["aws"]["instances"]["default"] = {
            "config": {"data": {}}
        }

        with pytest.raises(
            ValueError, match="Provider config 'data' must not be empty"
        ):
            ToolConfig.model_validate(payload)

    def test_default_instance_must_not_have_alias(self):
        payload = _base_tool_config_payload()
        payload["provider_mappings"]["aws"]["instances"]["default"] = {
            "alias": "default",
            "config": {"data": {"region": "us-east-1"}},
        }

        with pytest.raises(
            ValueError, match="default instance must not define an alias"
        ):
            ToolConfig.model_validate(payload)

    def test_non_default_instance_must_define_alias(self):
        payload = _base_tool_config_payload()
        payload["provider_mappings"]["aws"]["instances"]["secondary"] = {
            "config": {"data": {"region": "us-west-2"}}
        }

        with pytest.raises(ValueError, match="must define an alias"):
            ToolConfig.model_validate(payload)

    def test_provider_alias_must_be_unique_within_family(self):
        payload = _base_tool_config_payload()
        payload["provider_mappings"]["aws"]["instances"]["secondary"] = {
            "alias": "shared",
            "config": {"data": {"region": "us-west-2"}},
        }
        payload["provider_mappings"]["aws"]["instances"]["dr"] = {
            "alias": "shared",
            "config": {"data": {"region": "eu-west-1"}},
        }

        with pytest.raises(ValueError, match="is duplicated in one family"):
            ToolConfig.model_validate(payload)


class TestModuleProviderMappingValidation:
    def test_unknown_provider_family_reference_is_rejected(self):
        payload = _base_tool_config_payload()
        payload["module_mappings"]["aws_s3_bucket"]["providers"] = {
            "aws": "gcp.default"
        }

        with pytest.raises(ValueError, match="unknown provider family"):
            ToolConfig.model_validate(payload)

    def test_unknown_provider_instance_reference_is_rejected(self):
        payload = _base_tool_config_payload()
        payload["module_mappings"]["aws_s3_bucket"]["providers"] = {
            "aws": "aws.secondary"
        }

        with pytest.raises(ValueError, match="unknown provider instance"):
            ToolConfig.model_validate(payload)

    def test_invalid_provider_reference_format_is_rejected(self):
        payload = _base_tool_config_payload()
        payload["module_mappings"]["aws_s3_bucket"]["providers"] = {"aws": "aws"}

        with pytest.raises(ValueError, match="<provider>.<instance>"):
            ToolConfig.model_validate(payload)

    def test_valid_provider_reference_is_accepted(self):
        payload = _base_tool_config_payload()
        payload["provider_mappings"]["aws"]["instances"]["secondary"] = {
            "alias": "secondary",
            "config": {"data": {"region": "us-west-2"}},
        }
        payload["module_mappings"]["aws_s3_bucket"]["providers"] = {
            "aws": "aws.secondary"
        }

        config = ToolConfig.model_validate(payload)

        assert (
            config.module_mappings["aws_s3_bucket"].providers["aws"] == "aws.secondary"
        )

    def test_provider_instance_config_can_use_provider_config_spec(self):
        payload = _base_tool_config_payload()
        payload["provider_mappings"]["aws"]["instances"]["default"] = {
            "config": {
                "inline": "def config(**context): return {'region': context['inputs']['aws_region']}"
            }
        }

        config = ToolConfig.model_validate(payload)

        assert isinstance(
            config.provider_mappings["aws"].instances["default"].config,
            ProviderConfigSpec,
        )
