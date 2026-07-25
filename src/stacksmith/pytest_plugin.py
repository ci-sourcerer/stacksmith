"""Pytest fixture for Stacksmith policy and transform tests."""

from pathlib import Path

import pytest

from .testing import StacksmithTestRunner


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the managed-config option used by the Stacksmith test fixture."""
    parser.addoption(
        "--stacksmith-config",
        dest="stacksmith_config",
        help="Path to stacksmith-config.yaml used by stacksmith_test_runner.",
    )


def _find_config_path(request: pytest.FixtureRequest) -> Path:
    configured_path = request.config.getoption("stacksmith_config")
    if configured_path:
        return Path(configured_path)

    for directory in request.path.parents:
        config_path = directory / "stacksmith-config.yaml"
        if config_path.is_file():
            return config_path

    raise pytest.UsageError(
        "stacksmith_test_runner could not find stacksmith-config.yaml. Pass "
        "--stacksmith-config <path> to pytest."
    )


@pytest.fixture
def stacksmith_test_runner(
    request: pytest.FixtureRequest,
) -> StacksmithTestRunner:
    """Provide a runner loaded from the nearest managed configuration file."""
    return StacksmithTestRunner.from_config(_find_config_path(request))
