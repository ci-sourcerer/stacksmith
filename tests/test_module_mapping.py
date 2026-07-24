import pytest
from stacksmith.exceptions import StacksmithConfigError
from stacksmith.models import ToolConfig
from stacksmith.module_mapping import resolve_module_mapping


def _config(
    *,
    module_mappings: dict | None = None,
    default_module_mapping: dict | None = None,
) -> ToolConfig:
    return ToolConfig.model_validate(
        {
            "backend": {"type": "local", "path": ".state"},
            "tools": {
                "tofu": {"version": "1.11.6"},
                "terragrunt": {"version": "1.0.6"},
            },
            "provider_mappings": {},
            "module_mappings": module_mappings or {},
            "default_module_mapping": default_module_mapping,
        }
    )


def _git_mapping(repo: str) -> dict:
    return {
        "source": {
            "source": "git",
            "data": {
                "repo": repo,
                "ref": "latest",
            },
        }
    }


def test_resolve_module_mapping_prefers_explicit_mapping():
    config = _config(
        module_mappings={
            "service": _git_mapping("https://github.com/org/explicit.git")
        },
        default_module_mapping=_git_mapping(
            "https://github.com/org/{{ undefined_name }}.git"
        ),
    )

    mapping = resolve_module_mapping(config, "service", "checkout")

    assert mapping.source.data.repo == "https://github.com/org/explicit.git"


def test_resolve_module_mapping_renders_type_and_name():
    config = _config(
        default_module_mapping={
            **_git_mapping(
                "https://github.com/org/{{ component_type | replace('-', '_') }}"
                "-{{ component_name }}.git"
            ),
            "auto_inject": True,
            "tags": ["default"],
            "properties": {"region": {"mapped_to": "aws_region"}},
        }
    )

    mapping = resolve_module_mapping(config, "web-service", "checkout")

    assert mapping.source.data.repo == "https://github.com/org/web_service-checkout.git"
    assert mapping.description is None
    assert mapping.auto_inject is True
    assert mapping.tags == {"default"}
    assert mapping.properties["region"].mapped_to == "aws_region"


def test_resolve_module_mapping_uses_type_as_name_for_inspection():
    config = _config(
        default_module_mapping=_git_mapping(
            "https://github.com/org/{{ component_name }}.git"
        )
    )

    mapping = resolve_module_mapping(config, "service")

    assert mapping.source.data.repo == "https://github.com/org/service.git"


def test_resolve_module_mapping_rejects_undefined_template_value():
    config = _config(
        default_module_mapping=_git_mapping(
            "https://github.com/org/{{ missing_value }}.git"
        )
    )

    with pytest.raises(StacksmithConfigError, match="missing_value"):
        resolve_module_mapping(config, "service", "checkout")


def test_resolve_module_mapping_revalidates_rendered_source():
    config = _config(default_module_mapping=_git_mapping("{{ component_type }}"))

    with pytest.raises(StacksmithConfigError, match="must start with"):
        resolve_module_mapping(config, "service", "checkout")


def test_resolve_module_mapping_without_default_raises():
    config = _config(
        module_mappings={
            "database": _git_mapping("https://github.com/org/database.git")
        }
    )

    with pytest.raises(StacksmithConfigError, match="service"):
        resolve_module_mapping(config, "service", "checkout")
