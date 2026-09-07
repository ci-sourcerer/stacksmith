import importlib.util
import tomllib
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "update_cli_reference",
    Path("scripts/update_cli_reference.py"),
)
assert _SPEC is not None
_UPDATE_CLI_REFERENCE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_UPDATE_CLI_REFERENCE)


def test_cli_reference_contains_nested_commands():
    reference = _UPDATE_CLI_REFERENCE.generate_cli_reference()

    assert "### `stacksmith plan`" in reference
    assert "### `stacksmith info graph`" in reference
    assert "### `stacksmith info modules-and-policies`" in reference
    assert "### `stacksmith ci environments`" in reference
    assert "### `stacksmith ci validate`" in reference
    assert "### `stacksmith operation destroy`" in reference
    assert (
        "--command {test,plan,apply,destroy,plan-operation,apply-operation}"
        in reference
    )
    assert "--phase {test,plan,apply,destroy,plan-operation,operation}" in reference
    assert "| `--dry-run` |" in reference
    assert "| `--validation-report-format` |" in reference


def test_docs_cli_reference_is_current():
    cli_reference = Path("docs/reference/cli.md").read_text(encoding="utf-8")

    assert (
        _UPDATE_CLI_REFERENCE.replace_generated_block(
            cli_reference,
            _UPDATE_CLI_REFERENCE.generate_cli_reference(),
        )
        == cli_reference
    )


def test_ci_docs_cover_destroy_safety_and_lifecycle():
    ci_docs = Path("docs/integrations/ci.md").read_text(encoding="utf-8")

    assert "examples/github-actions/stacksmith-destroy.yml" in ci_docs
    assert "first previews infrastructure with `plan --destroy`" in ci_docs
    assert "destroys the operation state before infrastructure" in ci_docs
    assert "rejects destructive execution on pull requests" in ci_docs
    assert "`destroy-plan.json`" in ci_docs
    assert "`COMMAND`: `plan`, `apply`, `destroy`" in ci_docs


def test_readme_is_a_concise_documentation_entrypoint():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert len(readme.splitlines()) < 100
    assert "https://stacksmith.ci-sourcerer.com/" in readme
    assert _UPDATE_CLI_REFERENCE.START_MARKER not in readme


def test_replace_generated_block_requires_cli_heading_when_markers_are_missing():
    with pytest.raises(ValueError, match="CLI reference"):
        _UPDATE_CLI_REFERENCE.replace_generated_block(
            "# Stacksmith\n",
            _UPDATE_CLI_REFERENCE.generate_cli_reference(),
        )


def test_zensical_site_has_basic_local_pages():
    with Path("zensical.toml").open("rb") as config_file:
        zensical_config = tomllib.load(config_file)
    docs_index = Path("docs/index.md")

    assert docs_index.exists()
    assert zensical_config["project"]["site_name"] == "Stacksmith"
    assert len(zensical_config["project"]["nav"]) == 7
    assert "Stacksmith" in docs_index.read_text(encoding="utf-8")


def test_canonical_documentation_pages_exist():
    expected_pages = {
        "docs/concepts.md",
        "docs/contributing.md",
        "docs/getting-started.md",
        "docs/guides/advanced-authoring.md",
        "docs/guides/docker.md",
        "docs/guides/execution.md",
        "docs/guides/managed-configuration.md",
        "docs/guides/stack-authoring.md",
        "docs/integrations/ci.md",
        "docs/reference/cli-usage.md",
        "docs/reference/cli.md",
        "docs/reference/python-api.md",
        "docs/roadmap.md",
    }

    assert all(Path(page).is_file() for page in expected_pages)


def test_docs_deployment_publishes_the_zensical_site_to_github_pages():
    workflow = Path(".github/workflows/docs-deploy.yml").read_text(encoding="utf-8")

    assert "actions/configure-pages@v6" in workflow
    assert "actions/upload-pages-artifact@v5" in workflow
    assert "path: site/" in workflow
    assert "actions/deploy-pages@v5" in workflow
    assert "pages: write" in workflow
    assert "id-token: write" in workflow


def test_replace_generated_block_replaces_existing_markers():
    assert (
        _UPDATE_CLI_REFERENCE.replace_generated_block(
            "before\n"
            f"{_UPDATE_CLI_REFERENCE.START_MARKER}\n"
            "stale\n"
            f"{_UPDATE_CLI_REFERENCE.END_MARKER}\n"
            "after\n",
            f"{_UPDATE_CLI_REFERENCE.START_MARKER}\n"
            "fresh\n"
            f"{_UPDATE_CLI_REFERENCE.END_MARKER}",
        )
        == "before\n"
        f"{_UPDATE_CLI_REFERENCE.START_MARKER}\n"
        "fresh\n"
        f"{_UPDATE_CLI_REFERENCE.END_MARKER}\n"
        "after\n"
    )
