import json
from copy import deepcopy
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any

from loguru import logger as LOGGER

from .component_selection import (
    build_component_selection_contexts,
    compile_component_expression,
    extract_component_tag_references,
    select_component_names,
)
from .exceptions import StacksmithConfigError
from .models import AssociationBindingSpec, AssociationRule, StackDefinition, ToolConfig
from .module_mapping import resolve_module_mapping


@dataclass(frozen=True)
class AssociationApplication:
    """One managed association contribution to a component property."""

    rule: str
    producers: tuple[str, ...]
    consumer: str
    property: str
    merge: str


@dataclass(frozen=True)
class AssociationResolution:
    """Effective stack and provenance from managed association resolution."""

    stack: StackDefinition
    applications: tuple[AssociationApplication, ...]


def _component_reference(component_name: str, output_name: str) -> str:
    return (
        f"{{{{ components[{json.dumps(component_name)}][{json.dumps(output_name)}] }}}}"
    )


def _validate_disabled_associations(stack: StackDefinition, config: ToolConfig) -> None:
    disabled = set(stack.disabled_associations)
    for component in stack.components.values():
        disabled.update(component.disabled_associations)
    if unknown := sorted(disabled - set(config.associations)):
        raise StacksmithConfigError(
            "Stack disables unknown managed associations: " + ", ".join(unknown)
        )


def _enabled_matches(
    rule_name: str,
    selector: str,
    contexts: dict[str, dict[str, Any]],
    stack: StackDefinition,
) -> list[str]:
    return [
        component_name
        for component_name in select_component_names(
            compile_component_expression(
                selector,
                f"selector for association '{rule_name}'",
            ),
            contexts,
        )
        if rule_name not in stack.components[component_name].disabled_associations
    ]


def _validate_cardinality(
    rule_name: str,
    rule: AssociationRule,
    producers: list[str],
) -> None:
    count = len(producers)
    if not {
        "exactly_one": count == 1,
        "zero_or_one": count <= 1,
        "one_or_more": count >= 1,
        "zero_or_more": True,
    }[rule.producers.cardinality]:
        raise StacksmithConfigError(
            f"Association '{rule_name}' requires producer cardinality "
            f"'{rule.producers.cardinality}', but matched {count}: "
            f"{', '.join(producers) or 'none'}"
        )


def _set_if_absent_value(
    rule: AssociationRule, references: list[str]
) -> str | list[str]:
    if rule.producers.cardinality in {"exactly_one", "zero_or_one"}:
        return references[0]
    return references


def _validate_binding_outputs(
    rule_name: str,
    binding: AssociationBindingSpec,
    producers: list[str],
    stack: StackDefinition,
    config: ToolConfig,
) -> None:
    for producer_name in producers:
        mapping = resolve_module_mapping(
            config,
            stack.components[producer_name].type,
            producer_name,
            repository_path=(
                stack.source_path.parent if stack.source_path is not None else None
            ),
        )
        if binding.output not in mapping.outputs and not mapping.auto_expose_outputs:
            raise StacksmithConfigError(
                f"Association '{rule_name}' references output '{binding.output}' "
                f"that component '{producer_name}' does not expose"
            )


def _apply_binding(
    rule_name: str,
    rule: AssociationRule,
    binding: AssociationBindingSpec,
    producers: list[str],
    consumer_name: str,
    stack: StackDefinition,
    generated_properties: dict[tuple[str, str], str],
) -> bool:
    consumer = stack.components[consumer_name]
    property_key = (consumer_name, binding.property)
    references = [
        _component_reference(producer_name, binding.output)
        for producer_name in producers
    ]
    if not references:
        return False

    if binding.merge == "set_if_absent":
        if binding.property in consumer.properties:
            if property_key in generated_properties:
                raise StacksmithConfigError(
                    f"Associations '{generated_properties[property_key]}' and "
                    f"'{rule_name}' both set component '{consumer_name}' property "
                    f"'{binding.property}'"
                )
            return False
        consumer.properties[binding.property] = _set_if_absent_value(rule, references)
        generated_properties[property_key] = rule_name
        return True

    existing = consumer.properties.get(binding.property)
    if existing is None:
        consumer.properties[binding.property] = references
    elif not isinstance(existing, list):
        raise StacksmithConfigError(
            f"Association '{rule_name}' cannot append to component "
            f"'{consumer_name}' property '{binding.property}' because its value "
            "is not a list"
        )
    else:
        for reference in references:
            if reference not in existing:
                existing.append(reference)
    generated_properties.setdefault(property_key, rule_name)
    return True


def _validate_acyclic_dependencies(
    stack: StackDefinition,
    dependencies: dict[str, set[str]],
) -> None:
    try:
        TopologicalSorter(
            {
                component_name: dependencies.get(component_name, set())
                for component_name in stack.components
            }
        ).prepare()
    except CycleError as exc:
        cycle = exc.args[1] if len(exc.args) > 1 else []
        raise StacksmithConfigError(
            "Managed associations create circular component dependencies"
            + (f": {' -> '.join(cycle)}" if cycle else "")
        ) from exc


def resolve_associations(
    stack: StackDefinition,
    config: ToolConfig,
) -> AssociationResolution:
    """Apply managed associations to a copy of a final rendered stack.

    Args:
        stack: Final rendered and layered stack definition.
        config: Managed configuration containing association rules.

    Returns:
        Effective stack and association application provenance.

    Raises:
        StacksmithConfigError: If selectors, cardinality, merge semantics, opt-outs,
            or generated dependency edges are invalid.
    """
    _validate_disabled_associations(stack, config)
    if not config.associations:
        return AssociationResolution(stack=stack, applications=())

    effective_stack = deepcopy(stack)
    referenced_tags = set()
    for rule in config.associations.values():
        referenced_tags.update(extract_component_tag_references(rule.producers.select))
        referenced_tags.update(extract_component_tag_references(rule.consumers.select))
    contexts = build_component_selection_contexts(
        effective_stack,
        config,
        referenced_tags,
    )
    applications = []
    generated_properties: dict[tuple[str, str], str] = {}
    dependencies: dict[str, set[str]] = {}
    for rule_name, rule in config.associations.items():
        if rule_name in effective_stack.disabled_associations:
            continue
        consumers = _enabled_matches(
            rule_name,
            rule.consumers.select,
            contexts,
            effective_stack,
        )
        if not consumers:
            continue
        producers = _enabled_matches(
            rule_name,
            rule.producers.select,
            contexts,
            effective_stack,
        )
        _validate_cardinality(rule_name, rule, producers)
        for binding in rule.bindings:
            _validate_binding_outputs(
                rule_name,
                binding,
                producers,
                effective_stack,
                config,
            )

        for consumer_name in consumers:
            for binding in rule.bindings:
                if not _apply_binding(
                    rule_name,
                    rule,
                    binding,
                    producers,
                    consumer_name,
                    effective_stack,
                    generated_properties,
                ):
                    continue
                dependencies.setdefault(consumer_name, set()).update(producers)
                applications.append(
                    AssociationApplication(
                        rule=rule_name,
                        producers=tuple(producers),
                        consumer=consumer_name,
                        property=binding.property,
                        merge=binding.merge,
                    )
                )

    _validate_acyclic_dependencies(effective_stack, dependencies)
    for application in applications:
        LOGGER.debug(
            "Applied association '{rule}' from {producers} to component "
            "'{consumer}' property '{property}' with merge mode '{merge}'",
            rule=application.rule,
            producers=list(application.producers),
            consumer=application.consumer,
            property=application.property,
            merge=application.merge,
        )
    return AssociationResolution(
        stack=effective_stack,
        applications=tuple(applications),
    )
