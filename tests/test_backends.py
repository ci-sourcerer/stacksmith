from pathlib import Path

import pytest

from stacksmith.api import generate_stack
from stacksmith.backends import resolve_backend
from stacksmith.exceptions import StacksmithConfigError
from stacksmith.loading import load_config
from stacksmith.models import StackDefinition


def _stack() -> StackDefinition:
    return StackDefinition(name="payments", tags={"production"})


def _resolver_config(source: str) -> str:
    return f"""
backend:
  {source}
default_module_mapping:
  source:
    source: registry
    data:
      address: example/default
      version: "1.0.0"
"""


def test_backend_inline_resolver_receives_inputs_and_stack_metadata(tmp_path: Path):
    config_path = tmp_path / "stacksmith-config.yaml"
    config_path.write_text(
        _resolver_config(
            """inline: |
    def config(**context):
        return {
            "type": "s3",
            "bucket": context["environment"]["STACKSMITH_TEST_BUCKET"],
            "region": "us-east-1",
        }"""
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("STACKSMITH_TEST_BUCKET", "platform-state")
        backend = resolve_backend(config, _stack(), {"environment": "prod"})

    assert backend.config == {
        "bucket": "platform-state",
        "region": "us-east-1",
    }


def test_backend_script_is_resolved_relative_to_managed_config(tmp_path: Path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "backend.py").write_text(
        "def config(**context):\n"
        "    return {\n"
        "        'type': 'local',\n"
        "        'path': '.state',\n"
        "    }\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "stacksmith-config.yaml"
    config_path.write_text(
        _resolver_config(
            """script:
    source: local
    data:
      path: scripts/backend.py"""
        ),
        encoding="utf-8",
    )

    backend = resolve_backend(
        load_config(config_path),
        _stack(),
        {"environment": "dev"},
    )

    assert backend.type == "local"


def test_generate_stack_uses_dynamic_backend_selection(tmp_path: Path):
    stack_path = tmp_path / "stack.yaml"
    stack_path.write_text(
        "name: payments\ncomponents:\n  marker:\n    type: terraform_data\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "stacksmith-config.yaml"
    config_path.write_text(
        _resolver_config(
            """inline: |
    def config(**context):
        return {
            "type": "s3",
            "bucket": f"state-{context['inputs']['environment']}",
            "region": "us-east-1",
        }"""
        ),
        encoding="utf-8",
    )

    output_dir = generate_stack(
        stack_path,
        config=[str(config_path)],
        input_layers=[("var", "environment=prod")],
        build_dir=tmp_path / "build",
    )

    assert '"bucket": "state-prod"' in (output_dir / "terragrunt.hcl.json").read_text(
        encoding="utf-8"
    )


def test_backend_resolver_requires_a_callable_config(tmp_path: Path):
    config_path = tmp_path / "stacksmith-config.yaml"
    config_path.write_text(
        _resolver_config("inline: 'result = {}'"),
        encoding="utf-8",
    )

    with pytest.raises(StacksmithConfigError, match="must define a callable"):
        resolve_backend(load_config(config_path), _stack(), {})


def test_legacy_direct_backend_shape_is_rejected(tmp_path: Path):
    config_path = tmp_path / "stacksmith-config.yaml"
    config_path.write_text(
        """
backend:
  type: local
  path: .state
default_module_mapping:
  source:
    source: registry
    data:
      address: example/default
      version: "1.0.0"
""",
        encoding="utf-8",
    )

    with pytest.raises(StacksmithConfigError, match="backend"):
        load_config(config_path)
