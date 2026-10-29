import json
import os

import pytest
import yaml

from stacksmith.api import inspect_configuration
from stacksmith.cli.main import _cmd_info_configuration
from stacksmith.cli.parser import build_parser
from stacksmith.enums import MergeMode
from stacksmith.exceptions import StacksmithConfigError
from stacksmith.models import MergePolicy, MergeRule
from stacksmith.provenance import ACTIVE_TRACE


@pytest.fixture
def inspection_files(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("STACKSMITH_"):
            monkeypatch.delenv(key)
    stack = tmp_path / "stack.yaml"
    config = tmp_path / "config.yaml"
    values = tmp_path / "vars.yaml"
    stack.write_text("""name: example
components:
  api:
    type: service
    properties:
      instance_type: '{{ inputs.size }}'
""")
    config.write_text(
        yaml.safe_dump(
            {
                "backend": {"data": {"type": "local", "path": ".state"}},
                "tools": {
                    "tofu": {"version": "1.11.6"},
                    "terragrunt": {"version": "1.0.6"},
                },
                "provider_mappings": {},
                "module_mappings": {
                    "service": {
                        "source": {
                            "source": "git",
                            "data": {
                                "repo": "https://example.com/module.git",
                                "ref": "v1",
                            },
                        },
                        "properties": {
                            "instance_type": {
                                "mapped_to": "machine_type",
                                "transform": {"jinja": "{{ property.value | upper }}"},
                            },
                            "region": {"default": "{{ inputs.region }}"},
                        },
                    }
                },
            }
        )
    )
    values.write_text(
        "size: small\nregion: east\nsettings:\n  tags: [base]\n  nested: {a: 1}\n"
    )
    return stack, config, values


def _inspect(files, **kwargs):
    return inspect_configuration(
        files[0], config=[str(files[1])], vars_file=[str(files[2])], **kwargs
    )


def test_input_precedence_merge_and_templates(inspection_files, monkeypatch):
    monkeypatch.setenv("STACKSMITH_VAR_SIZE", "medium")
    result = _inspect(
        inspection_files,
        input_layers=[("var", "size=large")],
        query="inputs.size",
        show_values=True,
    )
    assert result["value"] == "large"
    assert [
        event["source"] for event in result["events"] if event["action"] == "source"
    ] == [
        str(inspection_files[2]),
        "STACKSMITH_VAR_SIZE",
        "command line --var size",
    ]
    merged = _inspect(
        inspection_files,
        input_layers=[("var", 'settings={"tags":["override"],"nested":{"b":2}}')],
        query="inputs.settings",
        show_values=True,
    )
    assert merged["value"] == {"tags": ["base", "override"], "nested": {"a": 1, "b": 2}}
    assert any(event.get("decision") == "append list" for event in merged["events"])


@pytest.mark.parametrize(
    "query",
    [
        "components.api.properties.instance_type",
        "components.api.properties.machine_type",
    ],
)
def test_property_template_transform_and_rename(inspection_files, query):
    result = _inspect(inspection_files, query=query, show_values=True)
    assert result["value"] == "SMALL"
    assert {event["action"] for event in result["events"]} >= {
        "source",
        "stack template",
        "transform",
        "rename",
        "resolved",
    }
    assert any(event["path"] == "inputs.size" for event in result["events"])
    assert any(
        event["path"].startswith(
            "config.module_mappings.service.properties.instance_type"
        )
        for event in result["events"]
    )
    assert any(
        event["path"] == "stack.components.api.properties.instance_type"
        for event in result["events"]
    )


def test_defaults_and_effective_configuration_do_not_generate_files(inspection_files):
    result = _inspect(inspection_files, show_values=True)
    assert result["effective"]["components"]["api"]["properties"] == {
        "machine_type": "SMALL",
        "region": "east",
    }
    assert any(event["action"] == "managed default" for event in result["events"])
    assert not (inspection_files[0].parent / ".stacksmith" / "main.tf.json").exists()
    assert ACTIVE_TRACE.get() is None


def test_default_redaction_covers_effective_values_and_history(inspection_files):
    result = _inspect(inspection_files, input_layers=[("var", "size=super-secret")])
    assert "super-secret" not in json.dumps(result)
    assert "SUPER-SECRET" not in json.dumps(result)
    assert "{{ inputs" not in json.dumps(result)
    assert result["effective"]["inputs"]["size"] == "[REDACTED]"


def test_merge_policy_is_used_for_explanation(inspection_files):
    result = _inspect(
        inspection_files,
        input_layers=[("var", 'settings={"tags":["override"]}')],
        merge_mode=MergePolicy(
            default=MergeMode.DEEP,
            rules=[
                MergeRule(
                    select="scope == 'vars' && address == '/settings'",
                    mode=MergeMode.OVERRIDE,
                )
            ],
        ),
        query="inputs.settings",
        show_values=True,
    )
    assert result["value"] == {"tags": ["override"]}
    assert any(event.get("mode") == "override" for event in result["events"])


def test_invalid_query_and_failure_reset_trace(inspection_files):
    with pytest.raises(StacksmithConfigError, match="Unknown configuration address"):
        _inspect(inspection_files, query="inputs.missing")
    assert ACTIVE_TRACE.get() is None
    inspection_files[0].write_text("name: '{{ inputs.missing }}'")
    with pytest.raises(StacksmithConfigError):
        _inspect(inspection_files)
    assert ACTIVE_TRACE.get() is None


def test_runfile_and_cli_explanation(inspection_files, capsys):
    runfile = inspection_files[0].parent / "run.yaml"
    runfile.write_text(
        yaml.safe_dump(
            {
                "stacks": [
                    {"source": "local", "data": {"path": str(inspection_files[0])}}
                ],
                "configs": [
                    {"source": "local", "data": {"path": str(inspection_files[1])}}
                ],
                "vars": [
                    {"source": "local", "data": {"path": str(inspection_files[2])}},
                    {"source": "inline", "data": {"size": "runfile"}},
                ],
            }
        )
    )
    assert (
        _cmd_info_configuration(
            build_parser().parse_args(
                [
                    "info",
                    "explain",
                    "inputs.size",
                    "--runfile",
                    str(runfile),
                    "--var",
                    "size=cli",
                    "--format",
                    "json",
                    "--show-values",
                ]
            )
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["value"] == "cli"
    assert any(
        str(runfile) in event["source"] and event["value"] == "runfile"
        for event in result["events"]
    )
    assert ACTIVE_TRACE.get() is None


def test_required_and_automatic_input_injection(inspection_files, monkeypatch):
    data = yaml.safe_load(inspection_files[1].read_text())
    data["module_input_sets"] = {"shared": {"inputs": {"region": {"type": "string"}}}}
    data["required_module_input_sets"] = ["shared"]
    data["module_mappings"]["service"]["auto_inject_inputs"] = True
    inspection_files[1].write_text(yaml.safe_dump(data))
    monkeypatch.setattr(
        "stacksmith.generation.terraform.discover_module_variables",
        lambda *args, **kwargs: {"size"},
    )
    result = _inspect(inspection_files, show_values=True)
    assert result["effective"]["components"]["api"]["properties"]["size"] == "small"
    assert any(event["action"] == "automatic injection" for event in result["events"])
    assert any(
        event["action"] == "required input injection" for event in result["events"]
    )


def test_transitive_input_templates_and_environment_reads(
    inspection_files, monkeypatch
):
    monkeypatch.setenv("EXAMPLE_REGION", "private-region")
    inspection_files[2].write_text(
        "base: \"{{ env('EXAMPLE_REGION') }}\"\n" + inspection_files[2].read_text()
    )
    result = _inspect(
        inspection_files,
        input_layers=[("var", "region={{ inputs.base }}")],
        query="components.api.properties.region",
        show_values=True,
    )
    assert result["value"] == "private-region"
    assert any(event["path"] == "inputs.base" for event in result["events"])
    assert any(
        event["path"] == "environment.EXAMPLE_REGION" for event in result["events"]
    )
    assert "private-region" not in json.dumps(
        _inspect(
            inspection_files,
            input_layers=[
                ("var", "region={{ env('EXAMPLE_REGION') }}"),
            ],
        )
    )


def test_stack_and_config_layer_history(inspection_files):
    stack_layer = inspection_files[0].parent / "stack-layer.yaml"
    stack_layer.write_text(
        "components:\n  api:\n    properties:\n      instance_type: large\n"
    )
    config_layer = inspection_files[0].parent / "config-layer.yaml"
    config_layer.write_text(
        "module_mappings:\n  service:\n    properties:\n      instance_type:\n        mapped_to: renamed_type\n"
    )
    result = inspect_configuration(
        [inspection_files[0], stack_layer],
        config=[str(inspection_files[1]), str(config_layer)],
        vars_file=[str(inspection_files[2])],
        query="components.api.properties.renamed_type",
        show_values=True,
    )
    assert result["value"] == "LARGE"
    assert {
        event["source"]
        for event in result["events"]
        if event["path"] == "stack.components.api.properties.instance_type"
        and event["action"] == "source"
    } == {str(inspection_files[0]), str(stack_layer)}
    assert {
        event["source"]
        for event in result["events"]
        if event["path"]
        == "config.module_mappings.service.properties.instance_type.mapped_to"
        and event["action"] == "source"
    } == {str(inspection_files[1]), str(config_layer)}


@pytest.mark.parametrize(
    "command,format_name",
    [("effective", "json"), ("explain", "table"), ("effective", "table")],
)
def test_cli_effective_and_table_output(inspection_files, capsys, command, format_name):
    args = build_parser().parse_args(
        [
            "info",
            command,
            "--stack",
            str(inspection_files[0]),
            "--config",
            str(inspection_files[1]),
            "--vars",
            str(inspection_files[2]),
            "--format",
            format_name,
        ]
    )
    assert _cmd_info_configuration(args) == 0
    output = capsys.readouterr().out
    assert "SMALL" not in output
    assert "{{ inputs" not in output
    if format_name == "json":
        assert json.loads(output)["inputs"]["size"] == "[REDACTED]"
    else:
        assert "Address" in output
