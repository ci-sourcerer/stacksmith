from stacksmith.models import StacksmithTestManifest
from stacksmith.test_generation import StacksmithTestGenerator


def test_generate_pytest_module_includes_all_test_types() -> None:
    manifest = StacksmithTestManifest.model_validate(
        {
            "variable_policies": {
                "aws_region": [
                    {"name": "accepts", "value": "us-east-1", "expect": "pass"},
                    {"value": "eu-west-1", "expect": "fail"},
                ]
            },
            "plan_policies": {
                "ec2_requires_imdsv2": [
                    {
                        "name": "warns_with_context",
                        "resources": [],
                        "context": {"stack_name": "production", "tags": ["apps"]},
                        "expect": "warn",
                    }
                ]
            },
            "component_properties": {
                "aws_s3_bucket": {
                    "bucket_name": [
                        {
                            "name": "normalizes",
                            "value": "My_Bucket",
                            "inputs": {"environment": "prod"},
                            "expect": {
                                "output_name": "bucket",
                                "value": "prod-my-bucket",
                            },
                        }
                    ]
                }
            },
        }
    )

    generated = StacksmithTestGenerator(manifest).generate_pytest_module()

    assert generated.test_count == 4
    assert "InputValidationOutcome.PASS" in generated.source
    assert "InputValidationOutcome.FAIL" in generated.source
    assert "PlanValidationOutcome.WARN" in generated.source
    assert "context=context" in generated.source
    assert (
        "run_component_property('aws_s3_bucket', 'bucket_name', value, inputs=inputs)"
        in generated.source
    )
    assert "assert result.output_name == 'bucket'" in generated.source

    compile(generated.source, "<generated-stacksmith-tests>", "exec")


def test_generate_pytest_module_includes_fixture_hooks() -> None:
    manifest = StacksmithTestManifest.model_validate(
        {
            "fixtures": {
                "setup": {
                    "inline": "fixture_state['setup'] = True",
                },
                "teardown": {
                    "script": {
                        "source": "local",
                        "data": {"path": "/tmp/test-fixture-teardown.py"},
                    }
                },
            },
            "variable_policies": {
                "aws_region": [
                    {"value": "us-east-1", "expect": "pass"},
                ]
            },
        }
    )

    generated = StacksmithTestGenerator(manifest).generate_pytest_module()

    assert "def _stacksmith_generated_fixtures()" in generated.source
    assert 'origin="fixtures.setup"' in generated.source
    assert 'origin="fixtures.teardown"' in generated.source
    assert "fixture_state['setup'] = True" in generated.source
    assert "/tmp/test-fixture-teardown.py" in generated.source
    assert '@pytest.fixture(scope="module", autouse=True)' in generated.source


def test_generate_pytest_module_uses_per_test_case_fixture_scope() -> None:
    manifest = StacksmithTestManifest.model_validate(
        {
            "fixtures": {
                "mode": "per-test-case",
                "setup": {
                    "inline": "fixture_state['setup'] = True",
                },
            },
            "variable_policies": {
                "aws_region": [
                    {"value": "us-east-1", "expect": "pass"},
                ]
            },
        }
    )

    generated = StacksmithTestGenerator(manifest).generate_pytest_module()

    assert '@pytest.fixture(scope="function", autouse=True)' in generated.source
