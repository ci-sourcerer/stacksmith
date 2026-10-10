import pytest
from jinja2 import Undefined

from stacksmith.templating import _TemplateEnvProxy


def test_env_without_default_returns_undefined(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("STACKSMITH_TEMPLATE_TEST", raising=False)

    assert isinstance(_TemplateEnvProxy()("STACKSMITH_TEMPLATE_TEST"), Undefined)


@pytest.mark.parametrize("default", [None, False, 0, "", {"fallback": []}])
def test_env_preserves_explicit_default(monkeypatch: pytest.MonkeyPatch, default):
    monkeypatch.delenv("STACKSMITH_TEMPLATE_TEST", raising=False)

    assert _TemplateEnvProxy()("STACKSMITH_TEMPLATE_TEST", default) is default


def test_env_existing_value_overrides_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STACKSMITH_TEMPLATE_TEST", "")

    assert _TemplateEnvProxy()("STACKSMITH_TEMPLATE_TEST", None) == ""
