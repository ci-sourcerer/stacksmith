import json
from pathlib import Path

import pytest

from stacksmith.plan_redaction import (
    REDACTION_MARKER,
    redact_plan,
    redact_plan_file,
    redact_sensitive_plan_value,
)

_SECRET = "stacksmith-canary-credential"


def _resource_change() -> dict[str, object]:
    return {
        "address": "terraform_data.example",
        "mode": "managed",
        "type": "terraform_data",
        "name": "example",
        "provider_name": "terraform.io/builtin/terraform",
        "change": {
            "actions": ["update"],
            "before": {"input": {"password": _SECRET, "region": "us-east-1"}},
            "after": {"input": {"password": _SECRET, "region": "us-west-2"}},
            "after_unknown": {"input": {"password": True}},
            "before_sensitive": {"input": {"password": True}},
            "after_sensitive": {"input": {"password": True}},
            "replace_paths": [["input", _SECRET]],
            "importing": {"id": _SECRET},
            "generated_config": _SECRET,
        },
    }


def _plan() -> dict[str, object]:
    return {
        "format_version": "1.0",
        "terraform_version": "1.12.5",
        "timestamp": "2026-07-28T00:00:00Z",
        "applyable": True,
        "complete": True,
        "errored": False,
        "variables": {"password": {"value": _SECRET}},
        "configuration": {
            "root_module": {
                "variables": {"password": {"default": _SECRET, "sensitive": True}},
                "resources": [{"expressions": {"input": {"constant_value": _SECRET}}}],
            }
        },
        "planned_values": {
            "outputs": {
                "endpoint": {
                    "value": "example.test",
                    "type": "string",
                    "sensitive": False,
                },
                "password": {
                    "value": _SECRET,
                    "type": "string",
                    "sensitive": True,
                },
            },
            "root_module": {
                "resources": [
                    {
                        "address": "terraform_data.example",
                        "mode": "managed",
                        "type": "terraform_data",
                        "name": "example",
                        "provider_name": "terraform.io/builtin/terraform",
                        "schema_version": 0,
                        "values": {
                            "input": {
                                "password": _SECRET,
                                "region": "us-east-1",
                            }
                        },
                        "sensitive_values": {"input": {"password": True}},
                    }
                ],
                "child_modules": [
                    {
                        "address": "module.child",
                        "resources": [
                            {
                                "address": "module.child.terraform_data.example",
                                "values": [_SECRET, "public"],
                                "sensitive_values": [True, False],
                            }
                        ],
                    }
                ],
            },
        },
        "prior_state": {
            "format_version": "1.0",
            "terraform_version": "1.12.5",
            "values": {
                "outputs": {
                    "password": {
                        "value": _SECRET,
                        "type": "string",
                        "sensitive": True,
                    }
                },
                "root_module": {
                    "resources": [
                        {
                            "address": "terraform_data.example",
                            "values": {"password": _SECRET},
                            "sensitive_values": {"password": True},
                        }
                    ]
                },
            },
            "checks": [
                {
                    "status": "fail",
                    "instances": [
                        {
                            "status": "fail",
                            "problems": [{"message": _SECRET}],
                        }
                    ],
                }
            ],
        },
        "resource_changes": [_resource_change()],
        "resource_drift": [_resource_change()],
        "output_changes": {
            "password": {
                "change": {
                    "actions": ["update"],
                    "before": _SECRET,
                    "after": _SECRET,
                    "before_sensitive": True,
                    "after_sensitive": True,
                }
            }
        },
        "deferred_changes": [
            {
                "reason": "provider_config_unknown",
                "resource_change": _resource_change(),
            }
        ],
        "checks": [
            {
                "address": {
                    "kind": "resource",
                    "to_display": "terraform_data.example",
                },
                "status": "fail",
                "instances": [
                    {
                        "address": {"to_display": "terraform_data.example"},
                        "status": "fail",
                        "problems": [{"message": _SECRET}],
                    }
                ],
            }
        ],
        "proposed_unknown": {"unsafe": _SECRET},
        "relevant_attributes": [{"resource": "example", "attribute": _SECRET}],
        "future_plan_field": {"unsafe": _SECRET},
    }


def test_redact_plan_removes_sensitive_and_unannotated_values():
    redacted = redact_plan(_plan())

    assert _SECRET not in json.dumps(redacted)
    assert "variables" not in redacted
    assert "configuration" not in redacted
    assert "proposed_unknown" not in redacted
    assert "relevant_attributes" not in redacted
    assert "future_plan_field" not in redacted
    assert redacted["planned_values"]["outputs"]["password"]["value"] == (
        REDACTION_MARKER
    )
    assert redacted["planned_values"]["outputs"]["endpoint"]["value"] == (
        "example.test"
    )
    assert redacted["planned_values"]["root_module"]["resources"][0]["values"] == {
        "input": {
            "password": REDACTION_MARKER,
            "region": "us-east-1",
        }
    }
    assert redacted["planned_values"]["root_module"]["child_modules"][0]["resources"][
        0
    ]["values"] == [REDACTION_MARKER, "public"]
    assert (
        redacted["resource_changes"][0]["change"]["before"]["input"]["password"]
        == REDACTION_MARKER
    )
    assert redacted["output_changes"]["password"]["change"]["after"] == (
        REDACTION_MARKER
    )
    assert "problems" not in redacted["checks"][0]["instances"][0]
    assert "replace_paths" not in redacted["resource_changes"][0]["change"]
    assert "importing" not in redacted["resource_changes"][0]["change"]
    assert "generated_config" not in redacted["resource_changes"][0]["change"]
    assert redacted["stacksmith_redaction"] == {
        "marker": REDACTION_MARKER,
        "policy": "archive",
        "version": 1,
    }


def test_redact_sensitive_plan_value_fails_closed_for_mismatched_shape():
    assert (
        redact_sensitive_plan_value(
            _SECRET,
            {"nested": True},
        )
        == REDACTION_MARKER
    )


def test_redact_sensitive_plan_value_fails_closed_for_nested_mismatched_shape():
    assert redact_sensitive_plan_value(
        {"nested": [_SECRET]},
        {"nested": {"first": True}},
    ) == {"nested": REDACTION_MARKER}


@pytest.mark.parametrize("format_version", [None, "", "2.0"])
def test_redact_plan_rejects_unsupported_format(format_version):
    with pytest.raises(ValueError, match="format"):
        redact_plan({"format_version": format_version})


def test_redact_plan_rejects_malformed_known_sections_without_copying_them():
    with pytest.raises(ValueError, match="values"):
        redact_plan(
            {
                "format_version": "1.0",
                "planned_values": _SECRET,
            }
        )


def test_redact_plan_file_writes_separate_and_in_place_outputs(tmp_path: Path):
    input_path = tmp_path / "plan.json"
    output_path = tmp_path / "redacted-plan.json"
    input_path.write_text(json.dumps(_plan()), encoding="utf-8")

    redact_plan_file(input_path, output_path)

    assert _SECRET in input_path.read_text(encoding="utf-8")
    assert _SECRET not in output_path.read_text(encoding="utf-8")

    redact_plan_file(input_path, input_path)

    assert _SECRET not in input_path.read_text(encoding="utf-8")
