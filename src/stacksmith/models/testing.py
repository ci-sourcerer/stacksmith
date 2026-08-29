from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .configuration import FileReference


def _normalize_input_validation_expectation(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"pass", "fail"}:
        raise ValueError("Input test expectation must be one of: pass, fail")
    return normalized


def _normalize_plan_validation_expectation(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"pass", "warn", "fail"}:
        raise ValueError("Plan test expectation must be one of: pass, warn, fail")
    return normalized


class FixtureSpec(BaseModel):
    """Reusable test fixture setup or teardown definition."""

    inline: str | None = None
    script: FileReference | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> FixtureSpec:
        if (self.inline is None) == (self.script is None):
            raise ValueError(
                "Exactly one of 'inline' or 'script' must be set for a fixture"
            )
        return self


class StacksmithTestFixtures(BaseModel):
    """Optional setup and teardown hooks for generated pytest suites."""

    mode: Literal["per-suite", "per-test-case"] = "per-suite"
    setup: FixtureSpec | None = None
    teardown: FixtureSpec | None = None

    @model_validator(mode="after")
    def _at_least_one_fixture(self) -> StacksmithTestFixtures:
        if self.setup is None and self.teardown is None:
            raise ValueError(
                "At least one of setup or teardown must be set when fixtures are configured"
            )
        return self


class VariablePolicyTestCase(BaseModel):
    """One variable validation test case."""

    name: str | None = None
    value: Any
    expect: str

    @field_validator("expect")
    @classmethod
    def _validate_expect(cls, value: str) -> str:
        return _normalize_input_validation_expectation(value)


class PlanTestResource(BaseModel):
    """Concise representation of one planned resource change.

    The generated OpenTofu plan change defaults to an address of
    `{type}.this` and a create action.
    """

    model_config = ConfigDict(extra="allow")

    type: str
    address: str | None = None
    actions: list[str] = Field(default_factory=lambda: ["create"], min_length=1)
    change: dict[str, Any] | None = None
    before: Any | None = None
    after: Any | None = None
    after_unknown: Any | None = None

    def to_plan_change(self) -> dict[str, Any]:
        """Render this shorthand resource into an OpenTofu plan resource change.

        Returns:
            Resource change data compatible with OpenTofu plan JSON.
        """
        change = dict(self.change or {})
        change.setdefault("actions", self.actions)
        for name in ("before", "after", "after_unknown"):
            value = getattr(self, name)
            if value is not None:
                change[name] = value
        return {
            **(self.model_extra or {}),
            "address": self.address or f"{self.type}.this",
            "type": self.type,
            "change": change,
        }


class PlanPolicyTestCase(BaseModel):
    """One plan validation test case."""

    name: str | None = None
    plan: dict[str, Any] | None = None
    resources: list[PlanTestResource] | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    expect: str

    @field_validator("expect")
    @classmethod
    def _validate_expect(cls, value: str) -> str:
        return _normalize_plan_validation_expectation(value)

    @model_validator(mode="after")
    def _validate_payload(self) -> PlanPolicyTestCase:
        if (self.plan is None) == (self.resources is None):
            raise ValueError("Exactly one of 'plan' or 'resources' must be set")
        return self


class ComponentPropertyExpectation(BaseModel):
    """Expected output for a configured component property test case."""

    value: Any
    output_name: str | None = None


class ComponentPropertyTestCase(BaseModel):
    """One configured component-property transform/validation test case."""

    name: str | None = None
    value: Any
    inputs: dict[str, Any] = Field(default_factory=dict)
    expect: ComponentPropertyExpectation


class StacksmithTestManifest(BaseModel):
    """Declarative test suite for `stacksmith test` YAML manifests."""

    description: str | None = None
    fixtures: StacksmithTestFixtures | None = None
    var_validations: dict[str, list[VariablePolicyTestCase]] = Field(
        default_factory=dict
    )
    plan_validations: dict[str, list[PlanPolicyTestCase]] = Field(default_factory=dict)
    component_properties: dict[str, dict[str, list[ComponentPropertyTestCase]]] = Field(
        default_factory=dict
    )
    source_path: Path | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _at_least_one_test_case(self) -> StacksmithTestManifest:
        if (
            not self.var_validations
            and not self.plan_validations
            and not self.component_properties
        ):
            raise ValueError(
                "A test manifest must define at least one test in var_validations, "
                "plan_validations, or component_properties"
            )
        return self
