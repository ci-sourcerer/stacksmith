from pathlib import Path

import pytest
from stacksmith.models import ValidationSpec
from stacksmith.variables import resolve_inputs


class TestResolveInputs:
    def test_vars_file_loaded(self, sample_values_yaml: Path):
        result = resolve_inputs(vars_file=sample_values_yaml)
        assert result["bucket_name"] == "my-bucket-from-file"
        assert result["instance_count"] == 3

    def test_vars_files_layer_in_order(self, tmp_path: Path):
        base_values_file = tmp_path / "base.yaml"
        base_values_file.write_text(
            (
                "bucket_name: base-bucket\n"
                "settings:\n"
                "  nested:\n"
                "    y: 20\n"
                "    z: 30\n"
                "items:\n"
                "  - one\n"
            ),
            encoding="utf-8",
        )
        override_values_file = tmp_path / "override.yaml"
        override_values_file.write_text(
            (
                "bucket_name: override-bucket\n"
                "settings:\n"
                "  nested:\n"
                "    x: 1\n"
                "    y: 99\n"
                "items:\n"
                "  - two\n"
            ),
            encoding="utf-8",
        )

        result = resolve_inputs(vars_file=[base_values_file, override_values_file])

        assert result["bucket_name"] == "override-bucket"
        assert result["settings"] == {"nested": {"x": 1, "y": 99, "z": 30}}
        assert result["items"] == ["one", "two"]

    def test_vars_default_from_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        base_values_file = tmp_path / "base.yaml"
        base_values_file.write_text("bucket_name: base-bucket\n", encoding="utf-8")
        override_values_file = tmp_path / "override.yaml"
        override_values_file.write_text(
            "bucket_name: override-bucket\n", encoding="utf-8"
        )
        monkeypatch.setenv(
            "STACKSMITH_VARS",
            f"{base_values_file}:{override_values_file}",
        )

        result = resolve_inputs()

        assert result["bucket_name"] == "override-bucket"

    def test_vars_file_map_merge_with_cli_override(self, tmp_path: Path):
        vars_file = tmp_path / "values.yaml"
        vars_file.write_text(
            "settings:\n  nested:\n    y: 20\n    z: 30\n",
            encoding="utf-8",
        )

        result = resolve_inputs(
            vars_file=vars_file,
            input_layers=[("var", 'settings={"nested": {"y": 99, "x": 1}}')],
        )

        assert result["settings"] == {"nested": {"y": 99, "z": 30, "x": 1}}

    def test_cli_var_list_appends_to_existing_vars(self, tmp_path: Path):
        vars_file = tmp_path / "values.yaml"
        vars_file.write_text(
            ("items:\n" "  - one\n"),
            encoding="utf-8",
        )

        result = resolve_inputs(
            vars_file=vars_file,
            input_layers=[("var", 'items=["two"]')],
        )

        assert result["items"] == ["one", "two"]

    def test_input_layers_deep_merge_in_cli_order(self, tmp_path: Path):
        base_vars_file = tmp_path / "base.yaml"
        base_vars_file.write_text(
            ("beep:\n" "  nested:\n" "    base: true\n" "  some:\n" "    - base\n"),
            encoding="utf-8",
        )
        override_vars_file = tmp_path / "override.yaml"
        override_vars_file.write_text(
            ("beep:\n" "  nested:\n" "    after: true\n" "  some:\n" "    - late\n"),
            encoding="utf-8",
        )

        result = resolve_inputs(
            vars_file=[],
            input_layers=[
                ("vars", str(base_vars_file)),
                ("var", 'beep={"nested": {"middle": true}, "some": ["thing"]}'),
                ("vars", str(override_vars_file)),
            ],
        )

        assert result["beep"] == {
            "nested": {"base": True, "middle": True, "after": True},
            "some": ["base", "thing", "late"],
        }

    def test_vars_files_override_mode_replaces_previous_values(self, tmp_path: Path):
        base_values_file = tmp_path / "base.yaml"
        base_values_file.write_text(
            (
                "settings:\n"
                "  nested:\n"
                "    y: 20\n"
                "    z: 30\n"
                "items:\n"
                "  - one\n"
            ),
            encoding="utf-8",
        )
        override_values_file = tmp_path / "override.yaml"
        override_values_file.write_text(
            ("settings:\n" "  nested:\n" "    x: 1\n" "items:\n" "  - two\n"),
            encoding="utf-8",
        )

        result = resolve_inputs(
            vars_file=[base_values_file, override_values_file],
            merge_mode="override",
        )

        assert result["settings"] == {"nested": {"x": 1}}
        assert result["items"] == ["two"]

    def test_input_layers_override_mode_replaces_values(self, tmp_path: Path):
        vars_file = tmp_path / "values.yaml"
        vars_file.write_text(
            "settings:\n  nested:\n    y: 20\n    z: 30\n",
            encoding="utf-8",
        )

        result = resolve_inputs(
            vars_file=vars_file,
            input_layers=[("var", 'settings={"nested": {"y": 99, "x": 1}}')],
            merge_mode="override",
        )

        assert result["settings"] == {"nested": {"y": 99, "x": 1}}

    def test_env_input_parsed_as_json(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("STACKSMITH_VAR_ENABLED", "true")
        monkeypatch.setenv("STACKSMITH_VAR_COUNT", "7")
        monkeypatch.setenv("STACKSMITH_VAR_TAGS", '{"team": "platform"}')

        result = resolve_inputs()

        assert result["enabled"] is True
        assert result["count"] == 7
        assert result["tags"] == {"team": "platform"}

    def test_cli_overrides_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("STACKSMITH_VAR_COUNT", "7")

        result = resolve_inputs(input_layers=[("var", "count=9")])

        assert result["count"] == 9

    def test_cli_non_json_value_remains_string(self):
        result = resolve_inputs(input_layers=[("var", "bucket_name=my-bucket-cli")])
        assert result["bucket_name"] == "my-bucket-cli"

    def test_stack_context_renders_runfile_var(self):
        result = resolve_inputs(
            input_layers=[("var", "bucket_name=app-{{ stack.name }}")],
            context={"stack": {"name": "payments", "tags": ["frontend"]}},
        )

        assert result["bucket_name"] == "app-payments"

    def test_inputs_can_reference_other_inputs(self):
        result = resolve_inputs(
            input_layers=[
                ("var", "prefix=prod"),
                ("var", "bucket_name={{ inputs.prefix }}-app"),
            ],
            context={"stack": {"name": "payments", "tags": ["frontend"]}},
        )

        assert result["bucket_name"] == "prod-app"


class TestConfigLevelValidation:
    def test_config_validation_passes(self):
        result = resolve_inputs(
            input_layers=[("var", "name=prod-app")],
            config_validations={
                "name": ValidationSpec(
                    inline=(
                        "def validate(value, **context): "
                        "return 'pass' if value.startswith('prod-') else 'fail'"
                    )
                )
            },
        )
        assert result["name"] == "prod-app"

    def test_config_validation_fails(self):
        with pytest.raises(ValueError, match="config validation"):
            resolve_inputs(
                input_layers=[("var", "name=dev-app")],
                config_validations={
                    "name": ValidationSpec(
                        inline=(
                            "def validate(value, **context): "
                            "return 'pass' if value.startswith('prod-') else 'fail'"
                        )
                    )
                },
            )

    def test_config_validation_ignored_for_absent_input(self):
        result = resolve_inputs(
            config_validations={
                "optional_thing": ValidationSpec(
                    inline="def validate(value, **context): return 1 / 0"
                )
            },
        )
        assert "optional_thing" not in result

    def test_config_validation_script_path_resolves_relative_to_config(
        self, tmp_path: Path
    ):
        validators_dir = tmp_path / "validators"
        validators_dir.mkdir()
        (validators_dir / "prefix.py").write_text(
            "def validate(value, **context):\n"
            "    return 'pass' if value.startswith('prod-') else 'fail'\n",
            encoding="utf-8",
        )

        result = resolve_inputs(
            input_layers=[("var", "name=prod-app")],
            config_validations={
                "name": ValidationSpec(
                    script={
                        "source": "local",
                        "data": {"path": "validators/prefix.py"},
                    }
                )
            },
            config_validation_base_path=tmp_path,
        )

        assert result["name"] == "prod-app"
