from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def sample_stack_yaml() -> Path:
    return FIXTURES_DIR / "sample_stack.yaml"


@pytest.fixture
def sample_stack_json() -> Path:
    return FIXTURES_DIR / "sample_stack.json"


@pytest.fixture
def sample_config_yaml() -> Path:
    return FIXTURES_DIR / "sample_config.yaml"


@pytest.fixture
def sample_config_local_yaml() -> Path:
    return FIXTURES_DIR / "sample_config_local.yaml"


@pytest.fixture
def sample_values_yaml() -> Path:
    return FIXTURES_DIR / "sample_values.yaml"


@pytest.fixture
def monorepo_dir() -> Path:
    return FIXTURES_DIR / "monorepo"
