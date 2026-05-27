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
        payload = {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
        }
        if self.stack_name is not None:
            payload["stack_name"] = self.stack_name
        return payload
