from dataclasses import dataclass
from enum import StrEnum, auto


class InputValidationOutcome(StrEnum):
    """Allowed outcomes for input and property validations."""

    PASS = auto()
    FAIL = auto()


class PlanValidationOutcome(StrEnum):
    """Allowed outcomes for plan validations."""

    PASS = auto()
    WARN = auto()
    FAIL = auto()


@dataclass(frozen=True)
class PlanValidationResult:
    name: str
    status: PlanValidationOutcome
    message: str
    stack_name: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """Return a dict suitable for JSON reports."""
        payload = {
            "name": self.name,
            "status": self.status.value,
        }
        summary, _, detail = self.message.partition(" — ")
        payload["message"] = summary
        if detail:
            payload["detail"] = detail
        if self.stack_name is not None:
            payload["stack_name"] = self.stack_name
        return payload
