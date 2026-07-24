import json
from io import StringIO
from pathlib import Path

import pytest
from loguru import logger as LOGGER
from stacksmith import api
from stacksmith.exceptions import StacksmithConfigError
from stacksmith.loader import load_config, load_stack
from stacksmith.models import ComponentDefinition, DefaultModuleMapping, StackDefinition
from stacksmith.validation import PlanValidationOutcome


def _build_stack(
    stack_name: str,
    component_name: str,
    component_type: str,
    tags: set[str],
) -> StackDefinition:
    return StackDefinition(
        name=stack_name,
        components={
            component_name: ComponentDefinition(
                type=component_type,
                tags=tags,
                properties={},
            )
        },
    )


def _setup_run_stack_action_mocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
    component_tags: set[str],
) -> dict[str, object]:
    stack = load_stack(sample_stack_yaml)
    config = load_config(sample_config_yaml)
    stack.components["my-bucket"].tags = component_tags
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        api,
        "_load_runtime_config",
        lambda *args, **kwargs: (tmp_path / ".cache", [sample_config_yaml], config),
    )
    monkeypatch.setattr(api, "load_stack", lambda *args, **kwargs: stack)
    monkeypatch.setattr(api, "_find_stack_file", lambda path: path)

    def _fake_generate_single_stack(*args, **kwargs):
        calls["generated"] = True
        return tmp_path / ".stacksmith"

    def _fake_run_terragrunt(args, working_dir, **kwargs):
        calls["terragrunt"] = (args, working_dir)
        calls["terragrunt_kwargs"] = kwargs
        return 0

    monkeypatch.setattr(api, "_generate_single_stack", _fake_generate_single_stack)
    monkeypatch.setattr(api, "run_terragrunt", _fake_run_terragrunt)
    return calls


def _setup_run_all_stacks_mocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_config_yaml: Path,
    stack_dirs: dict[str, Path],
    stacks: dict[str, StackDefinition],
) -> dict[str, object]:
    config = load_config(sample_config_yaml)
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        api,
        "_load_runtime_config",
        lambda *args, **kwargs: (tmp_path / ".cache", [sample_config_yaml], config),
    )
    monkeypatch.setattr(
        api,
        "_generate_all_stacks",
        lambda *args, **kwargs: (tmp_path / ".stacksmith", stack_dirs, stacks),
    )

    def _fake_run_terragrunt_all_ordered(action, stack_build_dirs, **kwargs):
        calls["run"] = (action, stack_build_dirs, kwargs)
        return 0

    monkeypatch.setattr(
        api,
        "run_terragrunt_all_ordered",
        _fake_run_terragrunt_all_ordered,
    )
    return calls


def test_compile_tag_expression_rejects_invalid_syntax():
    with pytest.raises(StacksmithConfigError, match="Invalid --tag-expr"):
        api._compile_tag_expression("contains(tags")


def test_extract_tag_references_supports_dot_style():
    assert api._extract_tag_references("tag.prod") == {"prod"}
    assert api._extract_tag_references(
        "tag.experimental && contains(tags, 'prod')"
    ) == {"experimental"}


def test_compile_tag_expression_rejects_bracket_style_syntax():
    with pytest.raises(StacksmithConfigError, match="Invalid --tag-expr"):
        api._compile_tag_expression("tag['prod']")


def test_compute_stack_target_modules_uses_effective_tag_union(
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
):
    stack = load_stack(sample_stack_yaml)
    config = load_config(sample_config_yaml)

    stack.components["my-bucket"].tags = {"prod"}
    config.module_mappings["aws_s3_bucket"].tags = {"shared"}

    expression = api._compile_tag_expression(
        "contains(tags, 'prod') && contains(tags, 'shared')"
    )
    targets = api._compute_stack_target_modules(stack, config, expression)

    assert targets == ["module.my-bucket"]


def test_compute_stack_target_modules_uses_default_mapping_tags(
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
):
    stack = load_stack(sample_stack_yaml)
    config = load_config(sample_config_yaml)
    stack.components["my-bucket"].type = "convention-bucket"
    stack.components["my-bucket"].tags = {"prod"}
    config.default_module_mapping = DefaultModuleMapping.model_validate(
        {
            "source": {
                "source": "git",
                "data": {
                    "repo": "https://github.com/org/{{ component_type }}.git",
                    "ref": "latest",
                },
            },
            "tags": ["shared"],
        }
    )

    expression = api._compile_tag_expression(
        "contains(tags, 'prod') && contains(tags, 'shared')"
    )

    assert api._compute_stack_target_modules(stack, config, expression) == [
        "module.my-bucket"
    ]


def test_compute_stack_target_modules_filters_with_dot_style_tag_expression(
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
):
    stack = load_stack(sample_stack_yaml)
    config = load_config(sample_config_yaml)

    stack.components["my-bucket"].tags = {"prod", "shared"}

    expression = api._compile_tag_expression("tag.prod && tag.shared")
    targets = api._compute_stack_target_modules(stack, config, expression)

    assert targets == ["module.my-bucket"]


def test_compute_stack_target_modules_requires_boolean_results(
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
):
    stack = load_stack(sample_stack_yaml)
    config = load_config(sample_config_yaml)

    stack.components["my-bucket"].tags = {"prod"}

    expression = api._compile_tag_expression("tag.prod && tag.shared")
    with pytest.raises(StacksmithConfigError, match="must evaluate to a boolean"):
        api._compute_stack_target_modules(stack, config, expression)


def test_compute_stack_target_modules_filters_with_required_tags(
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
):
    stack = load_stack(sample_stack_yaml)
    config = load_config(sample_config_yaml)

    stack.components["my-bucket"].tags = {"prod", "shared"}

    targets = api._compute_stack_target_modules(
        stack,
        config,
        required_tags={"prod", "shared"},
    )

    assert targets == ["module.my-bucket"]


def test_run_stack_action_appends_targets(
    monkeypatch,
    tmp_path: Path,
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
):
    calls = _setup_run_stack_action_mocks(
        monkeypatch,
        tmp_path,
        sample_stack_yaml,
        sample_config_yaml,
        {"prod"},
    )

    exit_code = api.run_stack_action(
        "plan",
        sample_stack_yaml,
        tag_expr="contains(tags, 'prod')",
    )

    assert exit_code == 0
    assert calls["generated"] is True
    assert calls["terragrunt"] == (
        ["plan", "-target", "module.my-bucket"],
        tmp_path / ".stacksmith",
    )


def test_run_stack_action_supports_direct_tag_expression(
    monkeypatch,
    tmp_path: Path,
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
):
    calls = _setup_run_stack_action_mocks(
        monkeypatch,
        tmp_path,
        sample_stack_yaml,
        sample_config_yaml,
        {"prod"},
    )

    exit_code = api.run_stack_action(
        "plan",
        sample_stack_yaml,
        tag_expr="tag.prod && tag.experimental == `false`",
    )

    assert exit_code == 0
    assert calls["terragrunt"] == (
        ["plan", "-target", "module.my-bucket"],
        tmp_path / ".stacksmith",
    )


def test_run_stack_action_supports_simple_tag_selectors(
    monkeypatch,
    tmp_path: Path,
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
):
    calls = _setup_run_stack_action_mocks(
        monkeypatch,
        tmp_path,
        sample_stack_yaml,
        sample_config_yaml,
        {"prod", "web"},
    )

    exit_code = api.run_stack_action(
        "plan",
        sample_stack_yaml,
        tags=["prod", "web"],
    )

    assert exit_code == 0
    assert calls["generated"] is True
    assert calls["terragrunt"] == (
        ["plan", "-target", "module.my-bucket"],
        tmp_path / ".stacksmith",
    )


def test_run_stack_action_no_cache_implies_no_cas(
    monkeypatch,
    tmp_path: Path,
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
):
    calls = _setup_run_stack_action_mocks(
        monkeypatch,
        tmp_path,
        sample_stack_yaml,
        sample_config_yaml,
        {"prod"},
    )

    exit_code = api.run_stack_action(
        "plan",
        sample_stack_yaml,
        no_cache=True,
    )

    assert exit_code == 0
    assert calls["terragrunt_kwargs"]["no_cas"] is True


def test_run_stack_action_combines_tags_and_expression_with_and_semantics(
    monkeypatch,
    tmp_path: Path,
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
):
    calls = _setup_run_stack_action_mocks(
        monkeypatch,
        tmp_path,
        sample_stack_yaml,
        sample_config_yaml,
        {"prod", "web"},
    )

    exit_code = api.run_stack_action(
        "plan",
        sample_stack_yaml,
        tags=["prod"],
        tag_expr="contains(tags, 'web')",
    )

    assert exit_code == 0
    assert calls["generated"] is True
    assert calls["terragrunt"] == (
        ["plan", "-target", "module.my-bucket"],
        tmp_path / ".stacksmith",
    )


def test_generate_stack_returns_output_path(
    monkeypatch,
    tmp_path: Path,
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
):
    config = load_config(sample_config_yaml)
    stack = load_stack(sample_stack_yaml)
    calls: dict[str, object] = {}
    monkeypatch.setattr(
        api,
        "_load_runtime_config",
        lambda *args, **kwargs: (tmp_path / ".cache", [sample_config_yaml], config),
    )
    monkeypatch.setattr(
        api,
        "_load_stack_definition",
        lambda *args, **kwargs: stack,
    )

    def _fake_generate_single_stack(loaded_stack, loaded_config, *args, **kwargs):
        calls["stack"] = loaded_stack
        calls["config"] = loaded_config
        return tmp_path / ".stacksmith"

    monkeypatch.setattr(
        api,
        "_generate_single_stack",
        _fake_generate_single_stack,
    )

    output_dir = api.generate_stack(sample_stack_yaml)

    assert output_dir == tmp_path / ".stacksmith"
    assert calls == {"stack": stack, "config": config}


def test_generate_stack_renders_component_template_before_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_config_yaml: Path,
):
    stack_file = tmp_path / "stack.yaml"
    stack_file.write_text(
        "name: workers\n"
        "components:\n"
        "{% for name in inputs.workers %}\n"
        "  '{{ name }}':\n"
        "    type: aws_s3_bucket\n"
        "    properties:\n"
        "      bucket: {{ name | tojson }}\n"
        "{% endfor %}\n",
        encoding="utf-8",
    )
    config = load_config(sample_config_yaml)
    generated = {}

    monkeypatch.setattr(
        api,
        "_load_runtime_config",
        lambda *args, **kwargs: (tmp_path / ".cache", [sample_config_yaml], config),
    )
    monkeypatch.setattr(
        api,
        "resolve_inputs",
        lambda *args, **kwargs: {"workers": ["blue", "green"]},
    )

    def _fake_generate_single_stack(stack, *_args, **_kwargs):
        generated["stack"] = stack
        return tmp_path / ".stacksmith"

    monkeypatch.setattr(api, "_generate_single_stack", _fake_generate_single_stack)

    api.generate_stack(stack_file)

    assert set(generated["stack"].components) == {"blue", "green"}
    assert generated["stack"].components["blue"].properties["bucket"] == "blue"


def test_generate_stack_exposes_git_repository_to_stack_templates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_config_yaml: Path,
):
    stack_file = tmp_path / "stack.yaml"
    stack_file.write_text(
        "name: repository-tag\n"
        "components:\n"
        "  bucket:\n"
        "    type: aws_s3_bucket\n"
        "    properties:\n"
        "      tags:\n"
        "        repository: '{{ git_repository }}'\n",
        encoding="utf-8",
    )
    config = load_config(sample_config_yaml)
    generated = {}

    monkeypatch.setattr(
        api,
        "_load_runtime_config",
        lambda *args, **kwargs: (tmp_path / ".cache", [sample_config_yaml], config),
    )
    monkeypatch.setattr(
        api,
        "get_current_git_repository",
        lambda path: "https://github.com/example/iac.git",
    )
    monkeypatch.setattr(
        api,
        "_generate_single_stack",
        lambda stack, *_args, **_kwargs: generated.setdefault("stack", stack),
    )

    api.generate_stack(stack_file)

    assert generated["stack"].components["bucket"].properties["tags"] == {
        "repository": "https://github.com/example/iac.git"
    }


def test_run_all_stacks_uses_stack_specific_target_args(
    monkeypatch,
    tmp_path: Path,
    sample_config_yaml: Path,
):
    matched = _build_stack("matched", "bucket", "aws_s3_bucket", {"prod"})
    skipped = _build_stack("skipped", "instance", "aws_ec2_instance", set())

    stack_dirs = {
        "matched": tmp_path / "matched",
        "skipped": tmp_path / "skipped",
    }
    stacks = {"matched": matched, "skipped": skipped}

    calls = _setup_run_all_stacks_mocks(
        monkeypatch,
        tmp_path,
        sample_config_yaml,
        stack_dirs,
        stacks,
    )

    exit_code = api.run_all_stacks(
        "plan",
        tmp_path,
        tag_expr="contains(tags, 'prod')",
    )

    assert exit_code == 0
    assert calls["run"][0] == ["plan"]
    assert calls["run"][1] == {"matched": tmp_path / "matched"}
    assert calls["run"][2]["stack_args_by_name"] == {
        "matched": ["plan", "-target", "module.bucket"]
    }


def test_run_all_stacks_supports_simple_tag_selectors(
    monkeypatch,
    tmp_path: Path,
    sample_config_yaml: Path,
):
    matched = _build_stack("matched", "bucket", "aws_s3_bucket", {"prod"})

    calls = _setup_run_all_stacks_mocks(
        monkeypatch,
        tmp_path,
        sample_config_yaml,
        {"matched": tmp_path / "matched"},
        {"matched": matched},
    )

    exit_code = api.run_all_stacks(
        "plan",
        tmp_path,
        tags=["prod"],
    )

    assert exit_code == 0
    assert calls["run"][0] == ["plan"]
    assert calls["run"][1] == {"matched": tmp_path / "matched"}
    assert calls["run"][2]["stack_args_by_name"] == {
        "matched": ["plan", "-target", "module.bucket"]
    }


def test_run_all_stacks_no_cache_implies_no_cas(
    monkeypatch,
    tmp_path: Path,
    sample_config_yaml: Path,
):
    matched = _build_stack("matched", "bucket", "aws_s3_bucket", {"prod"})

    calls = _setup_run_all_stacks_mocks(
        monkeypatch,
        tmp_path,
        sample_config_yaml,
        {"matched": tmp_path / "matched"},
        {"matched": matched},
    )

    exit_code = api.run_all_stacks(
        "plan",
        tmp_path,
        no_cache=True,
    )

    assert exit_code == 0
    assert calls["run"][2]["no_cas"] is True


def test_run_all_stacks_rejects_tag_selectors_for_init(
    monkeypatch,
    tmp_path: Path,
    sample_config_yaml: Path,
):
    config = load_config(sample_config_yaml)

    def _fake_load_runtime_config(*args, **kwargs):
        return tmp_path / ".cache", [sample_config_yaml], config

    monkeypatch.setattr(api, "_load_runtime_config", _fake_load_runtime_config)

    with pytest.raises(ValueError, match="--tag and --tag-expr"):
        api.run_all_stacks("init", tmp_path, tags=["prod"])


def test_run_all_stacks_passes_explicit_stack_refs_to_generator(
    monkeypatch,
    tmp_path: Path,
    sample_config_yaml: Path,
):
    config = load_config(sample_config_yaml)
    calls: dict[str, object] = {}

    monkeypatch.setattr(
        api,
        "_load_runtime_config",
        lambda *args, **kwargs: (tmp_path / ".cache", [sample_config_yaml], config),
    )

    def _fake_generate_all_stacks(*args, **kwargs):
        calls["generate"] = (args, kwargs)
        return tmp_path / ".stacksmith", {}, {}

    monkeypatch.setattr(api, "_generate_all_stacks", _fake_generate_all_stacks)
    monkeypatch.setattr(
        api,
        "run_terragrunt_all_ordered",
        lambda action, stack_build_dirs, **kwargs: 0,
    )

    exit_code = api.run_all_stacks(
        "apply",
        tmp_path,
        stacks=["./network/stack.yaml", "./app/stack.yaml"],
    )

    assert exit_code == 0
    assert calls["generate"][1]["stack_refs"] == [
        "./network/stack.yaml",
        "./app/stack.yaml",
    ]


def test_validate_stack_emits_single_json_report_block(
    monkeypatch,
    tmp_path: Path,
    sample_config_yaml: Path,
    capsys: pytest.CaptureFixture[str],
):
    config = load_config(sample_config_yaml)
    stack = _build_stack("sample", "bucket", "aws_s3_bucket", set())

    monkeypatch.setattr(
        api,
        "_load_runtime_config",
        lambda *args, **kwargs: (tmp_path / ".cache", [sample_config_yaml], config),
    )
    monkeypatch.setattr(api, "_find_stack_file", lambda path: path)
    monkeypatch.setattr(api, "load_stack", lambda *args, **kwargs: stack)
    monkeypatch.setattr(api, "load_stack_metadata", lambda *args, **kwargs: stack)
    monkeypatch.setattr(api, "resolve_inputs", lambda *args, **kwargs: {"ok": True})

    report = api.validate_stack(tmp_path / "stack.yaml")

    assert report["exit_code"] == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == report
    assert report["command"] == "validate"
    assert report["status"] == "pass"
    assert report["summary"] == {"pass": 1, "warn": 0, "fail": 0}


def test_validate_stack_emits_json_report_block_when_requested(
    monkeypatch,
    tmp_path: Path,
    sample_config_yaml: Path,
    capsys: pytest.CaptureFixture[str],
):
    config = load_config(sample_config_yaml)
    stack = _build_stack("sample", "bucket", "aws_s3_bucket", set())

    monkeypatch.setattr(
        api,
        "_load_runtime_config",
        lambda *args, **kwargs: (tmp_path / ".cache", [sample_config_yaml], config),
    )
    monkeypatch.setattr(api, "_find_stack_file", lambda path: path)
    monkeypatch.setattr(api, "load_stack", lambda *args, **kwargs: stack)
    monkeypatch.setattr(api, "load_stack_metadata", lambda *args, **kwargs: stack)
    monkeypatch.setattr(api, "resolve_inputs", lambda *args, **kwargs: {"ok": True})

    report = api.validate_stack(
        tmp_path / "stack.yaml",
        validation_report_format="json",
    )

    assert report["exit_code"] == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == report
    assert payload["command"] == "validate"
    assert payload["status"] == "pass"


def test_validate_stack_var_validation_failure_emits_json_report(
    monkeypatch,
    tmp_path: Path,
    sample_config_yaml: Path,
    capsys: pytest.CaptureFixture[str],
):
    config = load_config(sample_config_yaml)
    stack = _build_stack("sample", "bucket", "aws_s3_bucket", set())

    monkeypatch.setattr(
        api,
        "_load_runtime_config",
        lambda *args, **kwargs: (tmp_path / ".cache", [sample_config_yaml], config),
    )
    monkeypatch.setattr(api, "_find_stack_file", lambda path: path)
    monkeypatch.setattr(api, "load_stack", lambda *args, **kwargs: stack)
    monkeypatch.setattr(api, "load_stack_metadata", lambda *args, **kwargs: stack)
    monkeypatch.setattr(
        api,
        "resolve_inputs",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError(
                "Input 'region' failed config validation: region must be us-east-1"
            )
        ),
    )

    report = api.validate_stack(
        tmp_path / "stack.yaml",
        validation_report_format="json",
    )

    assert report["exit_code"] == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == report
    assert payload["status"] == "fail"
    assert payload["results"][0]["status"] == "fail"
    assert "Input 'region' failed config validation" in payload["results"][0]["message"]


def test_validate_stack_failure_logs_are_concise(
    monkeypatch,
    tmp_path: Path,
    sample_config_yaml: Path,
):
    config = load_config(sample_config_yaml)
    stack = _build_stack("sample", "bucket", "aws_s3_bucket", set())
    buffer = StringIO()
    sink_id = LOGGER.add(buffer, level="ERROR")

    try:
        monkeypatch.setattr(
            api,
            "_load_runtime_config",
            lambda *args, **kwargs: (
                tmp_path / ".cache",
                [sample_config_yaml],
                config,
            ),
        )
        monkeypatch.setattr(api, "_find_stack_file", lambda path: path)
        monkeypatch.setattr(api, "load_stack", lambda *args, **kwargs: stack)
        monkeypatch.setattr(
            api,
            "resolve_inputs",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                ValueError(
                    "Input 'region' failed config validation: region must be us-east-1"
                )
            ),
        )

        report = api.validate_stack(tmp_path / "stack.yaml")
        assert report["exit_code"] == 1

        log_text = buffer.getvalue()
        assert "see validation report for details" in log_text
        assert "Input 'region' failed config validation" not in log_text
    finally:
        LOGGER.remove(sink_id)


def test_run_stack_action_plan_emits_single_json_report_block(
    monkeypatch,
    tmp_path: Path,
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
    capsys: pytest.CaptureFixture[str],
):
    calls = _setup_run_stack_action_mocks(
        monkeypatch,
        tmp_path,
        sample_stack_yaml,
        sample_config_yaml,
        {"prod"},
    )

    def _fake_run_terragrunt(args, working_dir, **kwargs):
        kwargs["plan_validation_results"].append(
            api.PlanValidationResult(
                name="warn_rule",
                status=PlanValidationOutcome.WARN,
                message="policy warning — plan values: redacted",
                stack_name="my-stack",
            )
        )
        calls["terragrunt"] = (args, working_dir)
        return 0

    monkeypatch.setattr(api, "run_terragrunt", _fake_run_terragrunt)

    exit_code = api.run_stack_action(
        "plan",
        sample_stack_yaml,
        tag_expr="contains(tags, 'prod')",
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    report = payload
    assert report["command"] == "plan"
    assert report["status"] == "warn"
    assert report["summary"] == {"pass": 0, "warn": 1, "fail": 0}
    assert len(report["results"]) == 1


def test_run_stack_action_plan_emits_json_report_block_when_requested(
    monkeypatch,
    tmp_path: Path,
    sample_stack_yaml: Path,
    sample_config_yaml: Path,
    capsys: pytest.CaptureFixture[str],
):
    calls = _setup_run_stack_action_mocks(
        monkeypatch,
        tmp_path,
        sample_stack_yaml,
        sample_config_yaml,
        {"prod"},
    )

    def _fake_run_terragrunt(args, working_dir, **kwargs):
        kwargs["plan_validation_results"].append(
            api.PlanValidationResult(
                name="warn_rule",
                status=PlanValidationOutcome.WARN,
                message="policy warning — plan values: redacted",
                stack_name="my-stack",
            )
        )
        calls["terragrunt"] = (args, working_dir)
        return 0

    monkeypatch.setattr(api, "run_terragrunt", _fake_run_terragrunt)

    exit_code = api.run_stack_action(
        "plan",
        sample_stack_yaml,
        tag_expr="contains(tags, 'prod')",
        validation_report_format="json",
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["command"] == "plan"
    assert report["status"] == "warn"
    assert report["results"][0]["name"] == "warn_rule"
    assert report["results"][0]["message"] == "policy warning"
    assert report["results"][0]["detail"] == "plan values: redacted"


def test_run_all_plan_emits_single_json_report_block(
    monkeypatch,
    tmp_path: Path,
    sample_config_yaml: Path,
    capsys: pytest.CaptureFixture[str],
):
    matched = _build_stack("matched", "bucket", "aws_s3_bucket", {"prod"})
    stack_dirs = {"matched": tmp_path / "matched"}
    stacks = {"matched": matched}
    _setup_run_all_stacks_mocks(
        monkeypatch,
        tmp_path,
        sample_config_yaml,
        stack_dirs,
        stacks,
    )

    def _fake_run_terragrunt_all_ordered(action, stack_build_dirs, **kwargs):
        kwargs["plan_validation_results"].append(
            api.PlanValidationResult(
                name="fail_rule",
                status=PlanValidationOutcome.FAIL,
                message="policy failure — plan values: redacted",
                stack_name="matched",
            )
        )
        return 1

    monkeypatch.setattr(
        api, "run_terragrunt_all_ordered", _fake_run_terragrunt_all_ordered
    )

    exit_code = api.run_all_stacks("plan", tmp_path)

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    report = payload
    assert report["command"] == "run-all plan"
    assert report["status"] == "fail"
    assert report["summary"] == {"pass": 0, "warn": 0, "fail": 1}
    assert report["stack_count"] == 1


def test_run_all_plan_emits_json_report_block_when_requested(
    monkeypatch,
    tmp_path: Path,
    sample_config_yaml: Path,
    capsys: pytest.CaptureFixture[str],
):
    matched = _build_stack("matched", "bucket", "aws_s3_bucket", {"prod"})
    stack_dirs = {"matched": tmp_path / "matched"}
    stacks = {"matched": matched}
    _setup_run_all_stacks_mocks(
        monkeypatch,
        tmp_path,
        sample_config_yaml,
        stack_dirs,
        stacks,
    )

    def _fake_run_terragrunt_all_ordered(action, stack_build_dirs, **kwargs):
        kwargs["plan_validation_results"].append(
            api.PlanValidationResult(
                name="fail_rule",
                status=PlanValidationOutcome.FAIL,
                message="policy failure — plan values: redacted",
                stack_name="matched",
            )
        )
        return 1

    monkeypatch.setattr(
        api, "run_terragrunt_all_ordered", _fake_run_terragrunt_all_ordered
    )

    exit_code = api.run_all_stacks(
        "plan",
        tmp_path,
        validation_report_format="json",
    )

    assert exit_code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["command"] == "run-all plan"
    assert report["status"] == "fail"
    assert report["results"][0]["name"] == "fail_rule"
    assert report["results"][0]["message"] == "policy failure"
    assert report["results"][0]["detail"] == "plan values: redacted"


def test_diagnose_cache_writes_human_output_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_config_yaml: Path,
    capsys: pytest.CaptureFixture[str],
):
    config = load_config(sample_config_yaml)
    stack = _build_stack("sample", "bucket", "aws_s3_bucket", set())

    monkeypatch.setattr(
        api,
        "_load_runtime_config",
        lambda *args, **kwargs: (tmp_path / ".cache", [sample_config_yaml], config),
    )
    monkeypatch.setattr(api, "_find_stack_file", lambda path: path)
    monkeypatch.setattr(api, "load_stack", lambda *args, **kwargs: stack)
    monkeypatch.setattr(api, "load_stack_metadata", lambda *args, **kwargs: stack)
    monkeypatch.setattr(api, "get_vendor_dir", lambda: tmp_path / "vendor")

    exit_code = api.diagnose_cache(tmp_path / "stack.yaml")

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == ""
    assert "Stacksmith diagnostics" in captured.err
