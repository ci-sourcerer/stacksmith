from pathlib import Path

import pytest
from stacksmith.exceptions import StacksmithConfigError
from stacksmith.formatters import (
    render_file_reference_for,
    render_module_source_for,
    render_provider_source_for,
)
from stacksmith.models import (
    GitReference,
    LocalModuleSourceReference,
    LocalReference,
    ModuleGitSourceReference,
    ProviderSourceReference,
    RegistrySourceReference,
)


def test_render_file_reference_for_cli_local_source():
    rendered = render_file_reference_for(
        "cli",
        LocalReference(source="local", data={"path": "./vars.dev.yaml"}),
    )

    assert rendered == "./vars.dev.yaml"


def test_render_file_reference_for_cli_git_source():
    rendered = render_file_reference_for(
        "cli",
        GitReference(
            source="git",
            data={
                "repo": "https://github.com/org/shared.git",
                "path": "vars/base.yaml",
                "ref": "v1.2.3",
            },
        ),
    )

    assert rendered == "git+https://github.com/org/shared.git//vars/base.yaml@v1.2.3"


def test_render_file_reference_for_cli_path_input():
    rendered = render_file_reference_for("cli", Path("./stack.yaml"))

    assert rendered == "stack.yaml"


def test_render_module_source_for_terraform_git_source():
    rendered = render_module_source_for(
        "terraform",
        ModuleGitSourceReference(
            source="git",
            data={
                "repo": "https://github.com/org/terraform-aws-s3.git",
                "path": "modules/bucket",
                "ref": "1.0.0",
            },
        ),
    )

    assert rendered == {
        "source": "git::https://github.com/org/terraform-aws-s3.git//modules/bucket?ref=1.0.0"
    }


def test_render_module_source_for_terraform_registry_source():
    rendered = render_module_source_for(
        "terraform",
        RegistrySourceReference(
            source="registry",
            data={
                "address": "terraform-aws-modules/s3-bucket/aws",
                "version": "~> 5.0",
            },
        ),
    )

    assert rendered == {
        "source": "terraform-aws-modules/s3-bucket/aws",
        "version": "~> 5.0",
    }


def test_render_module_source_for_terraform_local_source_with_base_path():
    rendered = render_module_source_for(
        "terraform",
        LocalModuleSourceReference(
            source="local",
            data={"path": "examples/modules/helm_app"},
        ),
        options={"base_path": "."},
    )

    assert rendered == {"source": str(Path("examples/modules/helm_app").resolve())}
    assert "version" not in rendered


def test_render_provider_source_for_terraform_registry_source():
    rendered = render_provider_source_for(
        "terraform",
        ProviderSourceReference(
            source="registry",
            data={
                "address": "hashicorp/aws",
                "version": "~> 6.0",
            },
        ),
    )

    assert rendered == {
        "source": "hashicorp/aws",
        "version": "~> 6.0",
    }


def test_render_provider_source_for_terraform_registry_source_with_options():
    rendered = render_provider_source_for(
        "terraform",
        ProviderSourceReference(
            source="registry",
            data={
                "address": "hashicorp/aws",
                "version": "~> 6.0",
            },
        ),
        options={"unused": True},
    )

    assert rendered == {
        "source": "hashicorp/aws",
        "version": "~> 6.0",
    }


def test_render_module_source_for_unknown_target_raises_error():
    with pytest.raises(StacksmithConfigError, match="Unknown module source formatter"):
        render_module_source_for(
            "unknown",
            RegistrySourceReference(
                source="registry",
                data={
                    "address": "hashicorp/aws",
                    "version": "~> 6.0",
                },
            ),
        )
