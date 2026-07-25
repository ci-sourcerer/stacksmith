import json
from pathlib import Path

import pytest

from .enums import MergeMode
from .models import MergeConfig, MergePolicy, MergeRule
from .testing import StacksmithTestRunner


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the managed-config option used by the Stacksmith test fixture."""
    parser.addoption(
        "--stacksmith-config",
        dest="stacksmith_config",
        action="append",
        default=None,
        help=(
            "Path to stacksmith-config.yaml used by stacksmith_test_runner. "
            "Repeat to layer multiple configs."
        ),
    )
    parser.addoption(
        "--stacksmith-merge-mode",
        dest="stacksmith_merge_mode",
        choices=[mode.value for mode in MergeMode],
        default=MergeMode.DEEP.value,
        help="Merge strategy for layered Stacksmith test configs.",
    )
    parser.addoption(
        "--stacksmith-merge-rules-json",
        dest="stacksmith_merge_rules_json",
        default=None,
        help="JSON list of address-aware merge rules for Stacksmith test configs.",
    )
    parser.addoption(
        "--stacksmith-cache-dir",
        dest="stacksmith_cache_dir",
        type=Path,
        default=None,
        help="Cache directory for remote Stacksmith test resources.",
    )


def _find_config_paths(request: pytest.FixtureRequest) -> list[Path]:
    configured_paths = request.config.getoption("stacksmith_config")
    if configured_paths:
        return [Path(configured_path) for configured_path in configured_paths]

    for directory in request.path.parents:
        config_path = directory / "stacksmith-config.yaml"
        if config_path.is_file():
            return [config_path]

    raise pytest.UsageError(
        "stacksmith_test_runner could not find stacksmith-config.yaml. Pass one "
        "or more --stacksmith-config <path> options to pytest."
    )


def _merge_config(pytestconfig: pytest.Config) -> MergeConfig:
    merge_mode = MergeMode(pytestconfig.getoption("stacksmith_merge_mode"))
    raw_rules = pytestconfig.getoption("stacksmith_merge_rules_json")
    if raw_rules is None:
        return merge_mode

    try:
        rules_data = json.loads(raw_rules)
    except json.JSONDecodeError as exc:
        raise pytest.UsageError(
            f"Invalid --stacksmith-merge-rules-json value: {exc}"
        ) from exc

    if not isinstance(rules_data, list):
        raise pytest.UsageError(
            "--stacksmith-merge-rules-json must contain a JSON list of merge rules."
        )

    try:
        return MergePolicy(
            default=merge_mode,
            rules=[MergeRule.model_validate(rule) for rule in rules_data],
        )
    except ValueError as exc:
        raise pytest.UsageError(
            f"Invalid --stacksmith-merge-rules-json value: {exc}"
        ) from exc


@pytest.fixture
def stacksmith_test_runner(
    request: pytest.FixtureRequest,
) -> StacksmithTestRunner:
    """Provide a runner loaded from the effective managed configuration."""
    return StacksmithTestRunner.from_config_layers(
        _find_config_paths(request),
        merge_mode=_merge_config(request.config),
        cache_dir=request.config.getoption("stacksmith_cache_dir"),
    )
