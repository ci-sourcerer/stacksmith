from stacksmith.change_reports import change_report_console


def test_change_report_console_uses_configured_width(monkeypatch):
    monkeypatch.setenv("STACKSMITH_CONSOLE_WIDTH", "160")

    assert change_report_console().width == 160


def test_change_report_console_enforces_minimum_width(monkeypatch):
    monkeypatch.setenv("STACKSMITH_CONSOLE_WIDTH", "40")

    assert change_report_console().width == 80


def test_change_report_console_uses_normal_detection_without_valid_override(
    monkeypatch,
):
    monkeypatch.setenv("STACKSMITH_CONSOLE_WIDTH", "wide")
    monkeypatch.setenv("COLUMNS", "123")

    assert change_report_console().width == 123
