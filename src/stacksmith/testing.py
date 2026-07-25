"""Public helpers for testing managed Stacksmith policies and properties."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .exceptions import StacksmithNotFoundError
from .generator import _apply_property_spec, _build_property_context
from .loader import load_config
from .models import RemoteAuthConfig, ToolConfig
from .module_mapping import resolve_module_mapping
from .validation import (
    InputValidationOutcome,
    PlanValidationOutcome,
    validate_value,
    validate_value_with_outcome,
)


@dataclass(frozen=True)
class ComponentPropertyResult:
    """Result of evaluating one configured component property.

    Attributes:
        output_name: Module input name after the mapping has been applied.
        value: Value after its configured transform and validation have run.
    """

    output_name: str
    value: Any


class StacksmithTestRunner:
    """Run configured policies and component properties in pytest tests.

    The runner uses the production validation and property-processing code paths,
    including relative script resolution from the managed configuration.

    Args:
        config: Loaded Stacksmith managed configuration.
        cache_dir: Optional cache directory for remote policy or transform scripts.
        auth_config: Optional remote authentication override. The configuration's
            `remote_auth` values are used when this is not supplied.
    """

    def __init__(
        self,
        config: ToolConfig,
        cache_dir: Path | None = None,
        auth_config: RemoteAuthConfig | None = None,
    ) -> None:
        self._config = config
        self._cache_dir = cache_dir
        self._auth_config = config.remote_auth if auth_config is None else auth_config

    @classmethod
    def from_config(
        cls,
        config_path: Path | str,
        cache_dir: Path | None = None,
        auth_config: RemoteAuthConfig | None = None,
    ) -> "StacksmithTestRunner":
        """Load a managed configuration and create a test runner.

        Args:
            config_path: Path to the managed Stacksmith configuration.
            cache_dir: Optional cache directory for remote policy or transform
                scripts.
            auth_config: Optional remote authentication override.

        Returns:
            A runner backed by the loaded configuration.
        """
        return cls(load_config(Path(config_path)), cache_dir, auth_config)

    def run_plan_policy(
        self,
        name: str,
        plan: dict[str, Any],
        context: Mapping[str, Any] | None = None,
    ) -> tuple[PlanValidationOutcome, str]:
        """Run a named plan-validation policy, regardless of its enabled setting.

        Args:
            name: Key of the configured `plan_validations` policy.
            plan: Parsed OpenTofu plan JSON supplied to the policy.
            context: Extra policy context, such as `stack_name`.

        Returns:
            The normalized policy outcome and diagnostic message.

        Raises:
            StacksmithNotFoundError: If no plan-validation policy has the name.
        """
        policy = self._config.plan_validations.get(name)
        if policy is None:
            raise StacksmithNotFoundError(f"Plan validation policy not found: {name}")

        return validate_value_with_outcome(
            policy.rule,
            plan,
            base_path=self._config_directory,
            context={"kind": "plan_validation", "name": name, **(context or {})},
            cache_dir=self._cache_dir,
            auth_config=self._auth_config,
            allow_warn=True,
        )

    def run_variable_policy(
        self,
        name: str,
        value: Any,
    ) -> tuple[InputValidationOutcome, str]:
        """Run a named variable-validation policy.

        Args:
            name: Key of the configured `var_validations` policy.
            value: Resolved input value supplied to the policy.

        Returns:
            The normalized policy outcome and diagnostic message.

        Raises:
            StacksmithNotFoundError: If no variable-validation policy has the name.
        """
        policy = self._config.var_validations.get(name)
        if policy is None:
            raise StacksmithNotFoundError(
                f"Variable validation policy not found: {name}"
            )

        return validate_value(
            policy,
            value,
            base_path=self._config_directory,
            context={"name": name, "kind": "config_variable"},
            cache_dir=self._cache_dir,
            auth_config=self._auth_config,
        )

    def run_component_property(
        self,
        component_type: str,
        property_name: str,
        value: Any,
        component_name: str = "test-component",
        inputs: Mapping[str, Any] | None = None,
        stack: Mapping[str, Any] | None = None,
        git_repository: str | None = None,
        repository_path: Path | None = None,
    ) -> ComponentPropertyResult:
        """Apply a configured transform and validation for one component property.

        Args:
            component_type: Abstract component type configured in Stacksmith.
            property_name: Source property name configured on the component mapping.
            value: Property value before transform and validation.
            component_name: Component instance name exposed to policy context.
            inputs: Resolved input values exposed to transforms and validations.
            stack: Stack metadata exposed to transforms and validations.
            git_repository: Optional repository URL exposed to policy context.
            repository_path: Directory used to resolve a default module mapping.

        Returns:
            The mapped output name and transformed property value.

        Raises:
            StacksmithNotFoundError: If the mapping has no specification for the
                requested property.
        """
        mapping = resolve_module_mapping(
            self._config,
            component_type,
            component_name,
            repository_path=repository_path,
        )
        property_spec = mapping.properties.get(property_name)
        if property_spec is None:
            raise StacksmithNotFoundError(
                f"Component type '{component_type}' has no property policy for "
                f"'{property_name}'"
            )

        output_name = property_spec.mapped_to or property_name
        rendered = _apply_property_spec(
            value,
            property_spec,
            _build_property_context(
                name=property_name,
                kind="component_property",
                component_name=component_name,
                component_type=component_type,
                output_name=output_name,
                inputs=dict(inputs) if inputs is not None else None,
                stack=dict(stack) if stack is not None else None,
                git_repository=git_repository,
            ),
            self._config,
            cache_dir=self._cache_dir,
            auth_config=self._auth_config,
        )
        return ComponentPropertyResult(output_name=output_name, value=rendered)

    @property
    def _config_directory(self) -> Path | None:
        if self._config.source_path is None:
            return None
        return self._config.source_path.parent
