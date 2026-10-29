import json
from pathlib import Path
from unittest.mock import patch

import pytest

from stacksmith.exceptions import (
    StacksmithConfigError,
    StacksmithValidationExecutionError,
)
from stacksmith.loading import load_config, load_test_manifest
from stacksmith.models import (
    ModulePropertySpec,
    PlanValidation,
    StacksmithTestManifest,
    ValidationSpec,
)
from stacksmith.testing import (
    StacksmithTestGenerator,
    StacksmithTestRunner,
    find_untested_policies,
)
from stacksmith.validations import InputValidationOutcome, validate_value

pytest_plugins = ["pytester"]


def test_policy_inventory_includes_empty_and_disabled_policies(
    sample_config_local_yaml: Path,
) -> None:
    config = load_config(sample_config_local_yaml)
    config.var_validations = {
        "covered": ValidationSpec(inline="'pass'"),
        "empty": ValidationSpec(inline="'pass'"),
        "missing": ValidationSpec(inline="'pass'"),
    }
    config.plan_validations = {
        "disabled": PlanValidation(enabled=False, rule=ValidationSpec(inline="'pass'"))
    }
    assert find_untested_policies(
        config,
        StacksmithTestManifest.model_validate(
            {
                "var_validations": {
                    "covered": [{"value": 0, "expect": "pass"}],
                    "empty": [],
                }
            }
        ),
    ) == [
        "plan_validations.disabled",
        "var_validations.empty",
        "var_validations.missing",
    ]


def test_property_message_requires_expected_failure(tmp_path: Path) -> None:
    manifest = tmp_path / "tests.json"
    manifest.write_text(
        json.dumps(
            {
                "component_properties": {
                    "storage": {
                        "access": [
                            {
                                "value": "private",
                                "expect": {"value": "private"},
                                "message_contains": "private",
                            }
                        ]
                    }
                }
            }
        )
    )
    with pytest.raises(
        StacksmithConfigError, match="message_contains requires expect: fail"
    ):
        load_test_manifest(manifest)


@pytest.mark.parametrize(
    "source",
    [
        "raise RuntimeError('broken policy')",
        "def validate(value, **context):\n    raise RuntimeError('broken policy')",
        "def validate(:",
        "1 / 0",
        "'invalid status'",
        "'warn'",
    ],
)
def test_runner_raises_for_broken_variable_policy(
    sample_config_local_yaml: Path, source: str
) -> None:
    config = load_config(sample_config_local_yaml)
    config.var_validations["broken"] = ValidationSpec(inline=source)

    with pytest.raises(StacksmithValidationExecutionError):
        StacksmithTestRunner(config).run_variable_policy("broken", "value")

    assert validate_value(config.var_validations["broken"], "value")[0] == (
        InputValidationOutcome.FAIL
    )


def test_runner_raises_for_missing_policy_script(
    sample_config_local_yaml: Path, tmp_path: Path
) -> None:
    config = load_config(sample_config_local_yaml)
    config.plan_validations["broken"] = PlanValidation.model_validate(
        {
            "rule": {
                "script": {
                    "source": "local",
                    "data": {"path": str(tmp_path / "missing.py")},
                }
            }
        }
    )

    with pytest.raises(StacksmithValidationExecutionError, match="missing.py"):
        StacksmithTestRunner(config).run_plan_policy("broken", {})


def _run_generated_suite(
    pytester: pytest.Pytester,
    config_path: Path,
    manifest: dict[str, object],
) -> pytest.RunResult:
    pytester.makepyfile(
        StacksmithTestGenerator(
            load_test_manifest(
                pytester.makefile(".json", manifest=json.dumps(manifest))
            )
        )
        .generate_pytest_module()
        .source
    )
    return pytester.runpytest_subprocess("--stacksmith-config", str(config_path), "-q")


def test_generated_negative_cases_reject_policy_crashes(
    pytester: pytest.Pytester, sample_config_local_yaml: Path
) -> None:
    config = load_config(sample_config_local_yaml)
    config.var_validations = {
        "broken": ValidationSpec(inline="1 / 0"),
        "broken!": ValidationSpec(inline="'fail'"),
    }
    config.plan_validations = {
        "broken": PlanValidation(
            rule=ValidationSpec(inline="raise RuntimeError('plan crashed')")
        ),
        "rejects": PlanValidation(rule=ValidationSpec(inline="'fail'")),
    }
    _run_generated_suite(
        pytester,
        pytester.makefile(
            ".json",
            config=config.model_dump_json(exclude_none=True, exclude_unset=True),
        ),
        {
            "var_validations": {
                "broken": [{"name": "reject-invalid", "value": 0, "expect": "fail"}],
                "broken!": [{"name": "reject invalid", "value": 0, "expect": "fail"}],
            },
            "plan_validations": {
                "broken": [{"resources": [], "expect": "fail"}],
                "rejects": [{"resources": [], "expect": "fail"}],
            },
        },
    ).assert_outcomes(passed=2, failed=2)


@pytest.mark.parametrize(
    "reference",
    [
        {"source": "http", "data": {"url": "https://example.com/fixture.py"}},
        {
            "source": "git",
            "data": {
                "repo": "https://example.com/repo.git",
                "path": "fixture.py",
                "ref": "main",
            },
        },
    ],
)
def test_generated_remote_fixtures_are_resolved(
    sample_config_local_yaml: Path, tmp_path: Path, reference: dict[str, object]
) -> None:
    script = tmp_path / "fixture.py"
    script.write_text("def run(state):\n    state['ran'] = True\n")
    config = load_config(sample_config_local_yaml)
    namespace = {}
    exec(  # noqa: S102
        StacksmithTestGenerator(
            StacksmithTestManifest.model_validate(
                {
                    "fixtures": {"setup": {"script": reference}},
                    "var_validations": {"unused": [{"value": 0, "expect": "pass"}]},
                }
            )
        )
        .generate_pytest_module()
        .source,
        namespace,
    )
    with patch("stacksmith.remote.resolve_remote", return_value=script) as resolve:
        fixture = namespace["_stacksmith_generated_fixtures"].__wrapped__(
            StacksmithTestRunner(config, cache_dir=tmp_path)
        )
        next(fixture)
        assert namespace["_STACKSMITH_FIXTURE_STATE"] == {"ran": True}
        assert resolve.call_count == 1
        assert resolve.call_args.args[1:] == (tmp_path, config.remote_auth)
        with pytest.raises(StopIteration):
            next(fixture)
        assert namespace["_STACKSMITH_FIXTURE_STATE"] == {}


@pytest.mark.parametrize(
    "mode, expected_runs", [("per-suite", 1), ("per-test-case", 2)]
)
def test_generated_fixture_lifecycle(
    pytester: pytest.Pytester,
    sample_config_local_yaml: Path,
    mode: str,
    expected_runs: int,
) -> None:
    marker = pytester.path / "fixture-runs.txt"
    _run_generated_suite(
        pytester,
        sample_config_local_yaml,
        {
            "fixtures": {
                "mode": mode,
                "setup": {"inline": "fixture_state['ready'] = True"},
                "teardown": {
                    "inline": f"assert fixture_state['ready']\nwith open({str(marker)!r}, 'a') as stream:\n    stream.write('done\\n')"
                },
            },
            "component_properties": {
                "aws_s3_bucket": {
                    "acl": [{"value": "private", "expect": {"value": "private"}}] * 2
                }
            },
        },
    ).assert_outcomes(passed=2)
    assert marker.read_text().splitlines() == ["done"] * expected_runs


def test_generated_property_failures_and_context(
    pytester: pytest.Pytester, sample_config_local_yaml: Path
) -> None:
    config = load_config(sample_config_local_yaml)
    config.module_mappings["aws_s3_bucket"].properties = {
        "rejects": ModulePropertySpec.model_validate(
            {"validation": {"inline": "{'status': 'fail', 'message': 'private only'}"}}
        ),
        "crashes": ModulePropertySpec.model_validate(
            {"validation": {"inline": "1 / 0"}}
        ),
        "transform_crashes": ModulePropertySpec.model_validate(
            {"transform": {"inline": "1 / 0"}}
        ),
        "accepts": ModulePropertySpec(),
        "context": ModulePropertySpec.model_validate(
            {
                "mapped_to": "bucket",
                "transform": {
                    "inline": "def transform(value, **context):\n    return [value, context['component']['name'], context['inputs']['environment'], context['stack']['name'], context['env']['git_repository']]"
                },
            }
        ),
    }
    _run_generated_suite(
        pytester,
        pytester.makefile(
            ".json",
            config=config.model_dump_json(exclude_none=True, exclude_unset=True),
        ),
        {
            "component_properties": {
                "aws_s3_bucket": {
                    "rejects": [
                        {
                            "value": "public",
                            "expect": "fail",
                            "message_contains": "private only",
                        }
                    ],
                    "crashes": [{"value": "public", "expect": "fail"}],
                    "transform_crashes": [{"value": "public", "expect": "fail"}],
                    "accepts": [{"value": "public", "expect": "fail"}],
                    "context": [
                        {
                            "value": "input",
                            "component_name": "logs",
                            "inputs": {"environment": "prod"},
                            "stack": {"name": "production"},
                            "git_repository": "https://example.com/repo.git",
                            "expect": {
                                "output_name": "bucket",
                                "value": [
                                    "input",
                                    "logs",
                                    "prod",
                                    "production",
                                    "https://example.com/repo.git",
                                ],
                            },
                        }
                    ],
                }
            }
        },
    ).assert_outcomes(passed=2, failed=3)


def test_generated_policy_message_assertions(
    pytester: pytest.Pytester, sample_config_local_yaml: Path
) -> None:
    config = load_config(sample_config_local_yaml)
    config.var_validations = {
        "rejects": ValidationSpec(
            inline="{'status': 'fail', 'message': 'private only'}"
        ),
        "accepts": ValidationSpec(inline="{'status': 'pass', 'message': 'looks good'}"),
    }
    config.plan_validations = {
        "warns": PlanValidation(
            rule=ValidationSpec(inline="{'status': 'warn', 'message': 'review this'}")
        )
    }
    result = _run_generated_suite(
        pytester,
        pytester.makefile(
            ".json",
            config=config.model_dump_json(exclude_none=True, exclude_unset=True),
        ),
        {
            "var_validations": {
                "rejects": [
                    {
                        "value": "public",
                        "expect": "fail",
                        "message_contains": "private only",
                    },
                    {
                        "value": "public",
                        "expect": "fail",
                        "message_contains": "different reason",
                    },
                    {"value": "public", "expect": "pass"},
                ],
                "accepts": [
                    {
                        "value": "private",
                        "expect": "pass",
                        "message_contains": "looks good",
                    }
                ],
            },
            "plan_validations": {
                "warns": [
                    {
                        "resources": [],
                        "expect": "warn",
                        "message_contains": "review this",
                    }
                ]
            },
        },
    )
    result.assert_outcomes(passed=3, failed=2)
    result.stdout.fnmatch_lines(["*AssertionError: private only*"])
