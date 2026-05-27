from io import StringIO

from loguru import logger as LOGGER
from stacksmith.models import PlanValidation, ValidationSpec
from stacksmith.validation import (
    InputValidationOutcome,
    PlanValidationOutcome,
    PlanValidationResult,
    evaluate_plan_validations,
    evaluate_plan_validations_with_results,
    process_plan_validation_results,
    validate_value,
    validate_value_with_outcome,
)


class TestValidateValue:
    def test_plan_validation_supports_warn_string_outcome(self):
        status, msg = validate_value_with_outcome(
            ValidationSpec(inline="def validate(value, **context): return 'warn'"),
            {"resource_changes": []},
            context={"kind": "plan_validation", "stack_name": "example-stack"},
            allow_warn=True,
        )

        assert status == PlanValidationOutcome.WARN
        assert "Validation warning" in msg

    def test_plan_validation_supports_warn_dict_outcome_with_message(self):
        status, msg = validate_value_with_outcome(
            ValidationSpec(
                inline=(
                    "def validate(value, **context): "
                    "return {'status': 'warn', 'message': 'drift risk detected'}"
                )
            ),
            {"resource_changes": []},
            context={"kind": "plan_validation", "stack_name": "example-stack"},
            allow_warn=True,
        )

        assert status == PlanValidationOutcome.WARN
        assert "drift risk detected" in msg

    def test_valid_inline_expression(self):
        outcome, msg = validate_value(
            ValidationSpec(
                inline=(
                    "def validate(value, **context): "
                    "return 'pass' if value.startswith('my-bucket-') else 'fail'"
                )
            ),
            "my-bucket-test",
        )
        assert outcome == InputValidationOutcome.PASS
        assert msg == ""

    def test_invalid_inline_expression(self):
        outcome, msg = validate_value(
            ValidationSpec(
                inline=(
                    "def validate(value, **context): "
                    "return 'pass' if value.startswith('my-bucket-') else 'fail'"
                )
            ),
            "other-bucket",
        )
        assert outcome == InputValidationOutcome.FAIL
        assert "failed" in msg.lower()

    def test_invalid_plan_validation_message_is_concise(self):
        large_plan = {"resource_changes": [{"address": "aws_s3_bucket.example"}]}
        outcome, msg = validate_value(
            ValidationSpec(inline="def validate(value, **context): return 'fail'"),
            large_plan,
            context={"kind": "plan_validation"},
        )
        assert outcome == InputValidationOutcome.FAIL
        assert "Validation failed" in msg
        assert "resource_changes" not in msg

    def test_plan_validation_failure_includes_redacted_planned_values(self):
        plan_data = {
            "planned_values": {
                "outputs": {
                    "private_ip": {
                        "value": "192.168.3.2",
                        "type": "string",
                        "sensitive": False,
                    },
                    "db_password": {
                        "value": "supersecret",
                        "type": "string",
                        "sensitive": True,
                    },
                },
                "root_module": {
                    "resources": [
                        {
                            "address": "aws_instance.example[1]",
                            "mode": "managed",
                            "type": "aws_instance",
                            "name": "example",
                            "index": 1,
                            "provider_name": "aws",
                            "schema_version": 2,
                            "values": {
                                "id": "i-abc123",
                                "instance_type": "t2.micro",
                                "user_data": "do-not-leak",
                            },
                            "sensitive_values": {
                                "id": True,
                                "user_data": True,
                            },
                        }
                    ],
                    "child_modules": [
                        {
                            "address": "module.child",
                            "resources": [
                                {
                                    "address": "module.child.aws_db_instance.db",
                                    "values": {
                                        "endpoint": "db.example.internal",
                                        "password": "child-secret",
                                    },
                                    "sensitive_values": {"password": True},
                                }
                            ],
                        }
                    ],
                },
            }
        }

        outcome, msg = validate_value(
            ValidationSpec(inline="def validate(value, **context): return 'fail'"),
            plan_data,
            context={"kind": "plan_validation", "stack_name": "example-stack"},
        )

        assert outcome == InputValidationOutcome.FAIL
        assert "plan values:" in msg
        assert "stack_name=example-stack" in msg
        assert "private_ip" in msg
        assert "192.168.3.2" in msg
        assert "db_password" in msg
        assert "<sensitive>" in msg
        assert "aws_instance.example[1]" in msg
        assert "i-abc123" not in msg
        assert "do-not-leak" not in msg
        assert "module.child" in msg
        assert "child-secret" not in msg

    def test_variable_validation_includes_value_summary(self):
        outcome, msg = validate_value(
            ValidationSpec(
                inline=(
                    "def validate(value, **context): "
                    "return 'pass' if value.startswith('prod-') else 'fail'"
                )
            ),
            "staging-app",
            context={"kind": "stack_variable", "name": "app_name"},
        )
        assert outcome == InputValidationOutcome.FAIL
        assert "staging-app" in msg
        assert "value was" in msg

    def test_valid_numeric_range(self):
        outcome, _ = validate_value(
            ValidationSpec(
                inline=(
                    "def validate(value, **context): "
                    "return 'pass' if 1 <= value <= 10 else 'fail'"
                )
            ),
            5,
        )
        assert outcome == InputValidationOutcome.PASS

    def test_invalid_numeric_range(self):
        outcome, _ = validate_value(
            ValidationSpec(
                inline=(
                    "def validate(value, **context): "
                    "return 'pass' if 1 <= value <= 10 else 'fail'"
                )
            ),
            15,
        )
        assert outcome == InputValidationOutcome.FAIL

    def test_malformed_expression(self):
        outcome, msg = validate_value(
            ValidationSpec(
                inline="def validate(value, **context): return missing_name > 0"
            ),
            "test",
        )
        assert outcome == InputValidationOutcome.FAIL
        assert "missing_name" in msg

    def test_boolean_return_is_rejected(self):
        outcome, msg = validate_value(
            ValidationSpec(inline="def validate(value, **context): return value"), True
        )
        assert outcome == InputValidationOutcome.FAIL
        assert "must return" in msg

    def test_length_function(self):
        outcome, _ = validate_value(
            ValidationSpec(
                inline=(
                    "def validate(value, **context): "
                    "return 'pass' if len(value) > 3 else 'fail'"
                )
            ),
            "hello",
        )
        assert outcome == InputValidationOutcome.PASS

    def test_function_validation_returns_false(self):
        code = (
            "def validate(value, **context): "
            "return 'pass' if value.startswith('allowed-') else 'fail'"
        )
        outcome, _ = validate_value(ValidationSpec(inline=code), "disallowed-thing")
        assert outcome == InputValidationOutcome.FAIL

    def test_function_validation_returns_true(self):
        code = (
            "def validate(value, **context): "
            "return 'pass' if value.startswith('allowed-') else 'fail'"
        )
        outcome, _ = validate_value(ValidationSpec(inline=code), "allowed-thing")
        assert outcome == InputValidationOutcome.PASS

    def test_raise_in_block_is_caught(self):
        code = """\
def validate(value, **context):
    if not value.startswith('prod-'):
        raise ValueError(f"must start with prod-, got {value!r}")
"""
        outcome, msg = validate_value(ValidationSpec(inline=code), "dev-thing")
        assert outcome == InputValidationOutcome.FAIL
        assert "prod-" in msg

    def test_raise_in_block_passes_when_no_raise(self):
        code = """\
def validate(value, **context):
    if not value.startswith('prod-'):
        raise ValueError("bad prefix")
    return 'pass'
"""
        outcome, msg = validate_value(ValidationSpec(inline=code), "prod-thing")
        assert outcome == InputValidationOutcome.PASS
        assert msg == ""

    def test_inline_expression_supports_value_in_scope(self):
        outcome, msg = validate_value(
            ValidationSpec(
                inline="'pass' if value.startswith('prod-') and len(value) < 64 else 'fail'"
            ),
            "prod-bucket",
        )
        assert outcome == InputValidationOutcome.PASS
        assert msg == ""

    def test_inline_block_returns_result_variable(self):
        code = """\
if not value.startswith('prod-'):
    result = 'fail'
else:
    result = 'pass'
"""
        outcome, msg = validate_value(ValidationSpec(inline=code), "prod-thing")
        assert outcome == InputValidationOutcome.PASS
        assert msg == ""

    def test_script_validation_must_define_validate_function(self, tmp_path):
        script_path = tmp_path / "validators.py"
        script_path.write_text("x = 1 + 1\n", encoding="utf-8")

        outcome, msg = validate_value(
            ValidationSpec(script=str(script_path)),
            "anything",
            base_path=tmp_path,
        )

        assert outcome == InputValidationOutcome.FAIL
        assert "validate" in msg.lower()

    def test_builtin_access_available(self):
        outcome, _ = validate_value(
            ValidationSpec(
                inline=(
                    "def validate(value, **context): "
                    "return 'pass' if isinstance(value, list) and len(value) > 0 else 'fail'"
                )
            ),
            ["a"],
        )
        assert outcome == InputValidationOutcome.PASS

    def test_relative_script_path_is_resolved(self, tmp_path):
        script_dir = tmp_path / "validators"
        script_dir.mkdir()
        script_path = script_dir / "prefix_check.py"
        script_path.write_text(
            "def validate(value, **context):\n"
            "    return 'pass' if value.startswith('prod-') else 'fail'\n",
            encoding="utf-8",
        )

        outcome, msg = validate_value(
            ValidationSpec(script="validators/prefix_check.py"),
            "prod-app",
            base_path=tmp_path,
        )

        assert outcome == InputValidationOutcome.PASS
        assert msg == ""

    def test_missing_script_path_fails(self, tmp_path):
        outcome, msg = validate_value(
            ValidationSpec(script="validators/missing.py"),
            "prod-app",
            base_path=tmp_path,
        )

        assert outcome == InputValidationOutcome.FAIL
        assert "not found" in msg.lower()

    def test_context_values_are_available(self):
        code = (
            "def validate(value, **context): "
            "return 'pass' if context.get('kind') == 'stack_variable' "
            "and context.get('name') == 'bucket_name' else 'fail'"
        )
        outcome, msg = validate_value(
            ValidationSpec(inline=code),
            "ignored",
            context={"kind": "stack_variable", "name": "bucket_name"},
        )

        assert outcome == InputValidationOutcome.PASS
        assert msg == ""

    def test_validation_error_includes_resource_context(self):
        outcome, msg = validate_value(
            ValidationSpec(inline="def validate(value, **context): return 'fail'"),
            "ignored",
            context={
                "kind": "resource_property",
                "name": "bucket_name",
                "resource_name": "web",
                "resource_type": "aws_s3_bucket",
                "output_name": "bucket",
            },
        )

        assert outcome == InputValidationOutcome.FAIL
        assert "resource_property" in msg
        assert "resource_name=web" in msg
        assert "resource_type=aws_s3_bucket" in msg
        assert "output_name=bucket" in msg


class TestEvaluatePlanValidations:
    def test_process_plan_validation_results_warns_without_failing_by_default(self):
        buffer = StringIO()
        sink_id = LOGGER.add(buffer, level="WARNING")

        try:
            exit_code = process_plan_validation_results(
                [
                    PlanValidationResult(
                        name="warn_rule",
                        status=PlanValidationOutcome.WARN,
                        message="policy warning",
                        stack_name="example-stack",
                    )
                ],
                strict_validation_warnings=False,
            )

            assert exit_code == 0
            log_text = buffer.getvalue()
            assert "Plan validation 'warn_rule' warned" in log_text
            assert "policy warning" in log_text
            assert "Strict validation warning mode enabled" not in log_text
        finally:
            LOGGER.remove(sink_id)

    def test_process_plan_validation_results_strict_warning_mode_fails(self):
        buffer = StringIO()
        sink_id = LOGGER.add(buffer, level="WARNING")

        try:
            exit_code = process_plan_validation_results(
                [
                    PlanValidationResult(
                        name="warn_rule",
                        status=PlanValidationOutcome.WARN,
                        message="policy warning",
                        stack_name="example-stack",
                    )
                ],
                strict_validation_warnings=True,
            )

            assert exit_code == 1
            log_text = buffer.getvalue()
            assert "Plan validation 'warn_rule' warned" in log_text
            assert "Strict validation warning mode enabled" in log_text
        finally:
            LOGGER.remove(sink_id)

    def test_process_plan_validation_results_prefers_failures_over_warnings(self):
        buffer = StringIO()
        sink_id = LOGGER.add(buffer, level="WARNING")

        try:
            exit_code = process_plan_validation_results(
                [
                    PlanValidationResult(
                        name="warn_rule",
                        status=PlanValidationOutcome.WARN,
                        message="policy warning",
                        stack_name="example-stack",
                    ),
                    PlanValidationResult(
                        name="fail_rule",
                        status=PlanValidationOutcome.FAIL,
                        message="policy failure",
                        stack_name="example-stack",
                    ),
                ],
                strict_validation_warnings=False,
            )

            assert exit_code == 1
            log_text = buffer.getvalue()
            assert "Plan validation 'fail_rule' failed" in log_text
            assert "policy failure" in log_text
            assert "Plan validation 'warn_rule' warned" not in log_text
        finally:
            LOGGER.remove(sink_id)

    def test_structured_plan_results_include_warn_and_fail(self):
        results = evaluate_plan_validations_with_results(
            {
                "warn_rule": PlanValidation(
                    rule=ValidationSpec(
                        inline=(
                            "def validate(value, **context): "
                            "return {'status': 'warn', 'message': 'warning'}"
                        )
                    )
                ),
                "fail_rule": PlanValidation(
                    rule=ValidationSpec(
                        inline="def validate(value, **context): return 'fail'"
                    )
                ),
            },
            {"resource_changes": []},
            context={"stack_name": "example-stack"},
        )

        assert len(results) == 2
        assert {result.name for result in results} == {"warn_rule", "fail_rule"}
        assert any(result.status == PlanValidationOutcome.WARN for result in results)
        assert any(result.status == PlanValidationOutcome.FAIL for result in results)

    def test_collects_plan_validation_failures(self):
        failures = evaluate_plan_validations(
            {
                "no_destroy": PlanValidation(
                    description="Prevent destroy actions in plans",
                    rule=ValidationSpec(
                        inline=(
                            "def validate(value, **context): "
                            "return 'pass' if all(change['action'] != 'delete' "
                            "for change in value['changes']) else 'fail'"
                        )
                    ),
                )
            },
            {"changes": [{"action": "delete"}]},
        )

        assert len(failures) == 1
        assert "no_destroy" in failures[0]

    def test_passes_when_all_plan_validations_are_truthy(self):
        failures = evaluate_plan_validations(
            {
                "only_create": PlanValidation(
                    rule=ValidationSpec(
                        inline=(
                            "def validate(value, **context): "
                            "return 'pass' if all(change['action'] == 'create' "
                            "for change in value['changes']) else 'fail'"
                        )
                    )
                )
            },
            {"changes": [{"action": "create"}]},
        )

        assert failures == []

    def test_skips_disabled_plan_validations(self):
        failures = evaluate_plan_validations(
            {
                "disabled_rule": PlanValidation(
                    enabled=False,
                    rule=ValidationSpec(
                        inline="def validate(value, **context): return 'fail'"
                    ),
                )
            },
            {"changes": [{"action": "delete"}]},
        )

        assert failures == []

    def test_plan_validation_failure_includes_stack_name(self):
        failures = evaluate_plan_validations(
            {
                "bucket_naming_convention": PlanValidation(
                    rule=ValidationSpec(
                        inline="def validate(value, **context): return 'fail'"
                    ),
                )
            },
            {"resource_changes": []},
            context={"stack_name": "example-stack"},
        )

        assert len(failures) == 1
        assert "bucket_naming_convention" in failures[0]
        assert "example-stack" in failures[0]
        assert "Validation failed" in failures[0]

    def test_plan_validation_failure_includes_resource_summary(self):
        plan_data = {
            "resource_changes": [
                {
                    "address": "aws_s3_bucket.example",
                    "change": {"actions": ["create"]},
                },
                {
                    "address": "aws_s3_bucket.backup",
                    "change": {"actions": ["create"]},
                },
            ]
        }
        failures = evaluate_plan_validations(
            {
                "bucket_naming_convention": PlanValidation(
                    rule=ValidationSpec(
                        inline="def validate(value, **context): return 'fail'"
                    ),
                )
            },
            plan_data,
            context={"stack_name": "assets"},
        )

        assert len(failures) == 1
        msg = failures[0]
        assert "aws_s3_bucket.example" in msg
        assert "aws_s3_bucket.backup" in msg
        assert "2 creates" in msg

    def test_logs_plan_validation_strategy_for_passing_plan(self):
        buffer = StringIO()
        sink_id = LOGGER.add(buffer, level="DEBUG")

        try:
            failures = evaluate_plan_validations(
                {
                    "only_create": PlanValidation(
                        rule=ValidationSpec(
                            inline=(
                                "def validate(value, **context): "
                                "return 'pass' if all(change['action'] == 'create' "
                                "for change in value['changes']) else 'fail'"
                            )
                        )
                    )
                },
                {"changes": [{"action": "create"}]},
                context={"stack_name": "example-stack"},
            )

            assert failures == []
            log_text = buffer.getvalue()
            assert (
                "Evaluating plan validation 'only_create' for stack: example-stack"
                in log_text
            )
            assert "Plan validations passed for stack: example-stack" in log_text
        finally:
            LOGGER.remove(sink_id)

    def test_plan_validation_concurrency_respects_env_default(self, monkeypatch):
        monkeypatch.setenv("STACKSMITH_PLAN_VALIDATION_CONCURRENCY", "2")

        results = evaluate_plan_validations_with_results(
            {
                "one": PlanValidation(
                    rule=ValidationSpec(
                        inline="def validate(value, **context): return 'pass'"
                    )
                ),
                "two": PlanValidation(
                    rule=ValidationSpec(
                        inline="def validate(value, **context): return 'pass'"
                    )
                ),
            },
            {"resource_changes": []},
            context={"stack_name": "example-stack"},
        )

        assert len(results) == 2
        assert {result.name for result in results} == {"one", "two"}
