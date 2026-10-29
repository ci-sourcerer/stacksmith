import csv
import json
from io import StringIO

import pytest

from stacksmith.enums import ValidationReportFormat
from stacksmith.validation_reports import (
    parse_validation_report_csv,
    serialize_validation_report,
)


@pytest.fixture
def report():
    return {
        "command": "run-all plan",
        "status": "fail",
        "exit_code": 1,
        "strict_validation_warnings": True,
        "summary": {"pass": 1, "warn": 1, "fail": 1},
        "stack_count": 2,
        "run_id": "run-1",
        "plan_summary_path": "/build/plan-summary.json",
        "plan_complete": False,
        "change_totals": {
            "create": 2,
            "update": 0,
            "replace": 1,
            "destroy": 0,
            "resources": 3,
            "outputs": 0,
        },
        "results": [
            {
                "stack_name": "dev",
                "name": "allowed_regions",
                "status": "pass",
                "message": "Allowed region",
            },
            {
                "stack_name": "prod",
                "name": 'rule, "two"',
                "status": "warn",
                "message": 'Review "café", please\nSecond line\rFinal line',
                "detail": 'redacted values: {"secret": "<sensitive>"}',
            },
            {
                "stack_name": "prod",
                "name": "no_destroy",
                "status": "fail",
                "message": "Resource removal is forbidden",
            },
        ],
    }


@pytest.mark.parametrize("report_format", ["json", ValidationReportFormat.JSON])
def test_json_preserves_payload_and_pretty_printing(report, report_format):
    assert serialize_validation_report(report, report_format) == (
        f"{json.dumps(report, indent=2, sort_keys=True)}\n"
    )


@pytest.mark.parametrize("report_format", ["csv", ValidationReportFormat.CSV])
def test_csv_preserves_results_metadata_and_special_characters(report, report_format):
    output = serialize_validation_report(report, report_format)
    rows = list(csv.DictReader(StringIO(output)))

    assert len(rows) == 3
    assert list(rows[0])[:5] == ["stack_name", "name", "status", "message", "detail"]
    assert [row["status"] for row in rows] == ["pass", "warn", "fail"]
    assert rows[1]["message"] == report["results"][1]["message"]
    assert rows[1]["detail"] == report["results"][1]["detail"]
    assert all(row["run_id"] == "run-1" for row in rows)
    assert all(row["report_status"] == "fail" for row in rows)
    assert rows[0]["update_count"] == "0"
    assert rows[0]["strict_validation_warnings"] == "true"
    assert rows[0]["plan_complete"] == "false"
    assert parse_validation_report_csv(output) == report


@pytest.mark.parametrize("exit_code", [0, 7])
def test_empty_results_keep_overall_outcome(report, exit_code):
    report.update(
        results=[],
        exit_code=exit_code,
        status="pass" if exit_code == 0 else "fail",
        summary={"pass": 0, "warn": 0, "fail": 0},
    )
    output = serialize_validation_report(report, "csv")
    rows = list(csv.DictReader(StringIO(output)))

    assert len(rows) == 1
    assert rows[0]["name"] == rows[0]["status"] == ""
    assert rows[0]["report_status"] == report["status"]
    assert parse_validation_report_csv(output) == report


def test_single_stack_context_is_used_when_result_omits_it(report):
    report["stack_name"] = "dev"
    del report["results"][0]["stack_name"]

    rows = list(csv.DictReader(StringIO(serialize_validation_report(report, "csv"))))

    assert rows[0]["stack_name"] == "dev"
    assert rows[1]["stack_name"] == "prod"
    assert all(row["report_stack_name"] == "dev" for row in rows)


def test_unsupported_format_fails(report):
    with pytest.raises(ValueError):
        serialize_validation_report(report, "xml")


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("strict_validation_warnings", "yes"),
        ("plan_complete", "0"),
        ("exit_code", "invalid"),
        ("pass_count", "one"),
        ("status", "unknown"),
    ],
)
def test_csv_rejects_invalid_values(report, column, value):
    rows = list(csv.DictReader(StringIO(serialize_validation_report(report, "csv"))))
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    rows[0][column] = value
    writer.writerow(rows[0])

    with pytest.raises(ValueError):
        parse_validation_report_csv(output.getvalue())


def test_csv_rejects_metadata_from_different_invocations(report):
    rows = list(csv.DictReader(StringIO(serialize_validation_report(report, "csv"))))
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerow(rows[0])
    rows[1]["run_id"] = "another-run"
    writer.writerow(rows[1])

    with pytest.raises(ValueError, match="inconsistent report metadata"):
        parse_validation_report_csv(output.getvalue())


@pytest.mark.parametrize("text", ["", "name,status\nrule,pass\n"])
def test_csv_rejects_unexpected_columns(text):
    with pytest.raises(ValueError, match="unexpected columns"):
        parse_validation_report_csv(text)


def test_csv_rejects_header_without_report_row(report):
    with pytest.raises(ValueError, match="must contain"):
        parse_validation_report_csv(
            serialize_validation_report(report, "csv").splitlines(keepends=True)[0]
        )
