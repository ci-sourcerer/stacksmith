import csv
import json
from io import StringIO
from typing import Any

from .enums import ValidationReportFormat

_RESULT_COLUMNS = ("stack_name", "name", "status", "message", "detail")
_REPORT_COLUMNS = {
    "command": ("command",),
    "report_status": ("status",),
    "exit_code": ("exit_code",),
    "strict_validation_warnings": ("strict_validation_warnings",),
    "pass_count": ("summary", "pass"),
    "warn_count": ("summary", "warn"),
    "fail_count": ("summary", "fail"),
    "report_stack_name": ("stack_name",),
    "stack_count": ("stack_count",),
    "run_id": ("run_id",),
    "plan_summary_path": ("plan_summary_path",),
    "plan_complete": ("plan_complete",),
    "create_count": ("change_totals", "create"),
    "update_count": ("change_totals", "update"),
    "replace_count": ("change_totals", "replace"),
    "destroy_count": ("change_totals", "destroy"),
    "resource_count": ("change_totals", "resources"),
    "output_count": ("change_totals", "outputs"),
}
_CSV_COLUMNS = (*_RESULT_COLUMNS, *_REPORT_COLUMNS)
_BOOLEAN_COLUMNS = {"strict_validation_warnings", "plan_complete"}


def serialize_validation_report(
    report: dict[str, Any],
    report_format: str | ValidationReportFormat = ValidationReportFormat.JSON,
) -> str:
    """Serialize a validation report for stdout or an artifact file.

    Args:
        report: Validation report payload with an ordered `results` list.
        report_format: Output format, either `json` or `csv`.

    Returns:
        Serialized report ending with a newline. CSV repeats report metadata
        on each result row and emits a report-only row when results are empty.

    Raises:
        ValueError: If the requested format is unsupported.
    """
    if ValidationReportFormat(report_format) == ValidationReportFormat.JSON:
        return f"{json.dumps(report, indent=2, sort_keys=True)}\n"
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=_CSV_COLUMNS)
    writer.writeheader()
    metadata = {
        column: _report_value(report, path) for column, path in _REPORT_COLUMNS.items()
    }
    for result in report.get("results", []) or [{}]:
        writer.writerow(
            metadata
            | {column: result.get(column, "") for column in _RESULT_COLUMNS}
            | {"stack_name": result.get("stack_name", report.get("stack_name", ""))}
        )
    return output.getvalue()


def _report_value(report: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = report
    for key in path:
        value = value.get(key, "")
        if value == "" or value is None:
            return ""
    return json.dumps(value) if isinstance(value, bool) else value


def parse_validation_report_csv(text: str) -> dict[str, Any]:
    """Reconstruct a validation payload from Stacksmith's CSV format.

    Args:
        text: CSV report produced by `serialize_validation_report`.

    Returns:
        Report payload suitable for embedding in the CI JSON envelope.

    Raises:
        ValueError: If columns, rows, metadata, or result statuses are invalid.
        csv.Error: If the CSV syntax is invalid.
    """
    reader = csv.DictReader(StringIO(text, newline=""), strict=True)
    if reader.fieldnames != list(_CSV_COLUMNS):
        raise ValueError("Validation CSV has unexpected columns.")
    rows = list(reader)
    if not rows:
        raise ValueError("Validation CSV must contain a result or report-only row.")
    report = _parse_report_metadata(rows[0])
    report["results"] = []
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Validation CSV row has an unexpected number of fields.")
        if any(row[column] != rows[0][column] for column in _REPORT_COLUMNS):
            raise ValueError(
                "Validation CSV rows contain inconsistent report metadata."
            )
        if not row["name"]:
            if len(rows) != 1 or any(row[column] for column in _RESULT_COLUMNS[1:]):
                raise ValueError("Validation CSV has an invalid report-only row.")
            continue
        if row["status"] not in {"pass", "warn", "fail"}:
            raise ValueError("Validation CSV has an invalid result status.")
        report["results"].append(
            {"name": row["name"], "status": row["status"], "message": row["message"]}
            | {
                column: row[column]
                for column in ("stack_name", "detail")
                if row[column]
            }
        )
    return report


def _parse_report_metadata(row: dict[str, str]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for column, path in _REPORT_COLUMNS.items():
        if row[column] == "":
            continue
        if row[column] is None:
            raise ValueError("Validation CSV row is missing report metadata fields.")
        if len(path) == 1:
            report[path[0]] = _parse_report_value(column, row[column])
        else:
            report.setdefault(path[0], {})[path[1]] = _parse_report_value(
                column, row[column]
            )
    return report


def _parse_report_value(column: str, value: str) -> str | int | bool:
    if column in _BOOLEAN_COLUMNS:
        if value not in {"true", "false"}:
            raise ValueError(f"Validation CSV column '{column}' must be a boolean.")
        return value == "true"
    if column == "exit_code" or column.endswith("_count"):
        return int(value)
    return value
