from dataclasses import dataclass
from enum import StrEnum


class InputValidationOutcome(StrEnum):
    """Allowed outcomes for input and property validations."""

    PASS = "pass"
    FAIL = "fail"


class PlanValidationOutcome(StrEnum):
    """Allowed outcomes for plan validations."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class PlanValidationResult:
    name: str
    status: PlanValidationOutcome
    message: str
    stack_name: str | None = None

    def to_dict(self) -> dict[str, str]:
        """Return a dict suitable for JSON reports."""
        payload = {
            "name": self.name,
            "status": self.status.value,
        }
        # Split into short message and optional detail (em-dash separator used elsewhere)
        summary, sep, detail = self.message.partition(" — ")
        payload["message"] = summary
        if detail:
            payload["detail"] = detail
        if self.stack_name is not None:
            payload["stack_name"] = self.stack_name
        return payload
