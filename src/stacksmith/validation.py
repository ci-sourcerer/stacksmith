import asyncio
import textwrap
from enum import IntEnum
from pathlib import Path
from typing import Any

from loguru import logger as LOGGER

from .exceptions import (
    StacksmithError,
    StacksmithNotFoundError,
    StacksmithTransformError,
    StacksmithValidationError,
)
from .models import (
    PlanValidation,
    RemoteAuthConfig,
    TransformSpec,
    ValidationSpec,
)
from .remote import is_remote_url, resolve_remote
from .utils import stacksmith_env
from .validations.outcomes import (
    InputValidationOutcome,
    PlanValidationOutcome,
    PlanValidationResult,
)
from .validations.summarize import (
    _summarize_plan_resources,
    _summarize_plan_validation_value,
    _summarize_value,
)


def _load_inline_or_script_code(
    *,
    inline_code: str | None,
    script: str | None,
    inline_origin: str,
    invalid_spec_error: str,
    base_path: Path | None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> tuple[str, str]:
    if inline_code is not None and script is None:
        return inline_code, inline_origin
    if inline_code is None and script is not None:
        script_path = _resolve_script_path(
            script,
            base_path,
            cache_dir=cache_dir,
            auth_config=auth_config,
        )
        return script_path.read_text(encoding="utf-8"), str(script_path)
    raise StacksmithValidationError(invalid_spec_error)


def _format_validation_error(
    message: str,
    origin: str,
    context: dict[str, Any] | None = None,
    value_summary: str | None = None,
) -> str:
    if context:
        kind = context.get("kind")
        name = context.get("name")
        details: list[str] = []
        if kind:
            details.append(kind)
        if name:
            details.append(f"'{name}'")
        for key in ("stack_name", "resource_name", "resource_type", "output_name"):
            value = context.get(key)
            if value is not None:
                details.append(f"{key}={value}")
        if details:
            message = f"{message} [{' '.join(details)}]"
    if value_summary:
        message = f"{message} — {value_summary}"
    if origin:
        message = f"{message} (origin: {origin})"
    return message


def _resolve_script_path(
    script: str,
    base_path: Path | None,
    *,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> Path:
    if is_remote_url(script):
        if cache_dir is None:
            raise StacksmithValidationError(
                f"Cannot fetch remote script without a cache directory: {script}"
            )
        return resolve_remote(script, cache_dir, auth_config)

    script_path = Path(script)
    if not script_path.is_absolute():
        if base_path is None:
            raise StacksmithValidationError(
                f"Cannot resolve relative script path: {script}"
            )
        script_path = base_path / script_path

    script_path = script_path.resolve()
    if not script_path.exists():
        raise StacksmithNotFoundError(f"Script not found: {script_path}")
    return script_path


def _load_code(
    spec: ValidationSpec | TransformSpec,
    base_path: Path | None,
    *,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> tuple[str, str]:
    match spec:
        case ValidationSpec(inline=inline, script=script):
            return _load_inline_or_script_code(
                inline_code=inline,
                script=script,
                inline_origin="<inline-validation>",
                invalid_spec_error="Validation spec must define exactly one of 'inline' or 'script'.",
                base_path=base_path,
                cache_dir=cache_dir,
                auth_config=auth_config,
            )
        case TransformSpec(inline=inline, script=script):
            return _load_inline_or_script_code(
                inline_code=inline,
                script=script,
                inline_origin="<inline-transform>",
                invalid_spec_error="Invalid specification",
                base_path=base_path,
                cache_dir=cache_dir,
                auth_config=auth_config,
            )
        case _:
            raise StacksmithValidationError(
                "Spec must be ValidationSpec or TransformSpec"
            )


def _evaluate_inline_validation(
    code: str,
    origin: str,
    value: Any,
    context: dict[str, Any] | None,
) -> Any:
    ns: dict[str, Any] = {
        "value": value,
        "context": context or {},
    }

    try:
        expression = compile(code, origin, "eval")
    except SyntaxError:
        exec(compile(code, origin, "exec"), ns)  # noqa: S102
        if "result" in ns:
            return ns["result"]
        return None

    return eval(expression, ns)


def _normalize_outcome_status(raw_status: Any) -> str | None:
    if not isinstance(raw_status, str):
        return None

    normalized = raw_status.strip().lower()
    if not normalized:
        return None

    return normalized


def _resolve_plan_validation_concurrency() -> int:
    raw_value = stacksmith_env("PLAN_VALIDATION_CONCURRENCY")
    if raw_value is None:
        return 5
    try:
        value = int(raw_value)
        if value > 0:
            return value
    except ValueError:
        pass

    LOGGER.warning(
        "Ignoring invalid STACKSMITH_PLAN_VALIDATION_CONCURRENCY=%r; using default 5",
        raw_value,
    )
    return 5


class PlanValidationExitCode(IntEnum):
    PASS = 0
    FAIL = 1


def process_plan_validation_results(
    results: list[PlanValidationResult],
    *,
    strict_validation_warnings: bool,
) -> PlanValidationExitCode:
    """Log plan validation outcomes and return the process-style exit code.

    Args:
        results: Evaluated plan validation outcomes.
        strict_validation_warnings: When `True`, warning outcomes fail the run.

    Returns:
        `PlanValidationExitCode.FAIL` when failures exist or warnings are treated
        as failures; otherwise `PlanValidationExitCode.PASS`.
    """
    failures = [
        result for result in results if result.status == PlanValidationOutcome.FAIL
    ]
    warnings = [
        result for result in results if result.status == PlanValidationOutcome.WARN
    ]

    if failures:
        for failure in failures:
            stack_info = (
                f" for stack '{failure.stack_name}'" if failure.stack_name else ""
            )
            LOGGER.error(
                "Plan validation '{name}' failed{stack_info} (see validation report for details)",
                name=failure.name,
                stack_info=stack_info,
            )
        return PlanValidationExitCode.FAIL

    if not warnings:
        return PlanValidationExitCode.PASS

    for warning in warnings:
        stack_info = f" for stack '{warning.stack_name}'" if warning.stack_name else ""
        LOGGER.warning(
            "Plan validation '{name}' warned{stack_info} (see validation report for details)",
            name=warning.name,
            stack_info=stack_info,
        )

    if strict_validation_warnings:
        LOGGER.error(
            "Strict validation warning mode enabled and at least one warning was raised."
        )
        return PlanValidationExitCode.FAIL

    return PlanValidationExitCode.PASS


def _process_plan_validation(
    name: str,
    plan_validation: PlanValidation,
    plan_data: dict[str, Any],
    *,
    base_path: Path | None = None,
    context: dict[str, Any] | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    resource_summary: str,
    stack_name: str,
) -> PlanValidationResult:
    LOGGER.debug(
        "Evaluating plan validation '{name}' for stack: {stack_name}",
        name=name,
        stack_name=stack_name,
    )

    status, message = validate_value_with_outcome(
        plan_validation.rule,
        plan_data,
        base_path=base_path,
        context=context,
        cache_dir=cache_dir,
        auth_config=auth_config,
        allow_warn=True,
    )

    if status != PlanValidationOutcome.PASS:
        if "resource changes:" not in message:
            message = f"{message} (resource changes: {resource_summary})"

        if status == PlanValidationOutcome.FAIL:
            LOGGER.debug(
                "Plan validation '{name}' failed for stack: {stack_name}",
                name=name,
                stack_name=stack_name,
            )
            LOGGER.debug(
                "Redacted plan values for validation '{name}': {summary}",
                name=name,
                summary=_summarize_plan_validation_value(plan_data) or "<unavailable>",
            )
    else:
        LOGGER.debug(
            "Plan validation '{name}' passed for stack: {stack_name}",
            name=name,
            stack_name=stack_name,
        )

    return PlanValidationResult(
        name=name,
        status=status,
        message=message,
        stack_name=stack_name if stack_name is not None else None,
    )


async def _evaluate_plan_validation_task(
    name: str,
    plan_validation: PlanValidation,
    plan_data: dict[str, Any],
    *,
    base_path: Path | None = None,
    context: dict[str, Any] | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    resource_summary: str,
    stack_name: str,
    semaphore: asyncio.Semaphore,
) -> PlanValidationResult:
    async with semaphore:
        return await asyncio.to_thread(
            _process_plan_validation,
            name,
            plan_validation,
            plan_data,
            base_path=base_path,
            context=context,
            cache_dir=cache_dir,
            auth_config=auth_config,
            resource_summary=resource_summary,
            stack_name=stack_name,
        )


def _extract_outcome_status_and_message(
    raw_result: Any,
) -> tuple[str | None, str | None]:
    if isinstance(raw_result, dict):
        message_raw = raw_result.get("message")
        return _normalize_outcome_status(raw_result.get("status")), (
            message_raw
            if isinstance(message_raw, str) and message_raw.strip()
            else None
        )

    return _normalize_outcome_status(raw_result), None


def _invalid_outcome_contract_message(*, allow_warn: bool, raw_result: Any) -> str:
    status_values = "'pass', 'warn', or 'fail'" if allow_warn else "'pass' or 'fail'"
    return (
        f"Validation must return {status_values}, or a mapping with a 'status' key. "
        f"Received {_summarize_value(raw_result)!r}."
    )


def _coerce_validation_outcome(
    raw_result: Any,
    *,
    allow_warn: bool,
) -> tuple[PlanValidationOutcome, str | None]:
    normalized_status, message = _extract_outcome_status_and_message(raw_result)
    if normalized_status == PlanValidationOutcome.PASS.value:
        return PlanValidationOutcome.PASS, message
    if normalized_status == PlanValidationOutcome.FAIL.value:
        return PlanValidationOutcome.FAIL, message
    if normalized_status == PlanValidationOutcome.WARN.value:
        if allow_warn:
            return PlanValidationOutcome.WARN, message
        return (
            PlanValidationOutcome.FAIL,
            message
            or "Warnings are not supported for input validations; return 'pass' or 'fail'.",
        )

    return (
        PlanValidationOutcome.FAIL,
        _invalid_outcome_contract_message(
            allow_warn=allow_warn,
            raw_result=raw_result,
        ),
    )


def validate_value_with_outcome(
    spec: ValidationSpec,
    value: Any,
    *,
    base_path: Path | None = None,
    context: dict[str, Any] | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
    allow_warn: bool = False,
) -> tuple[PlanValidationOutcome, str]:
    try:
        code, origin = _load_code(
            spec, base_path, cache_dir=cache_dir, auth_config=auth_config
        )
        code = textwrap.dedent(code)
    except Exception as exc:
        return PlanValidationOutcome.FAIL, _format_validation_error(
            str(exc) if str(exc) else f"{type(exc).__name__} raised during validation",
            origin="<validation-spec>",
            context=context,
        )

    value_summary = None
    if context and context.get("kind") == "plan_validation":
        plan_value_summary = _summarize_plan_validation_value(value)
        if plan_value_summary is not None:
            value_summary = f"plan values: {plan_value_summary}"
    elif context:
        value_summary = f"value was {_summarize_value(value)!r}"

    ns: dict[str, Any] = (
        {"value": value, "context": context or {}} if spec.inline is not None else {}
    )
    try:
        exec(compile(code, origin, "exec"), ns)  # noqa: S102
    except Exception as exc:
        return PlanValidationOutcome.FAIL, _format_validation_error(
            str(exc) if str(exc) else f"{type(exc).__name__} raised during validation",
            origin,
            context,
            value_summary=value_summary,
        )

    validate_fn = ns.get("validate")
    raw_result: Any
    if callable(validate_fn):
        try:
            raw_result = validate_fn(value, **(context or {}))
        except Exception as exc:
            return PlanValidationOutcome.FAIL, _format_validation_error(
                (
                    str(exc)
                    if str(exc)
                    else f"{type(exc).__name__} raised during validation"
                ),
                origin,
                context,
                value_summary=value_summary,
            )
    elif spec.inline is not None:
        try:
            raw_result = _evaluate_inline_validation(code, origin, value, context)
        except Exception as exc:
            return PlanValidationOutcome.FAIL, _format_validation_error(
                (
                    str(exc)
                    if str(exc)
                    else f"{type(exc).__name__} raised during validation"
                ),
                origin,
                context,
                value_summary=value_summary,
            )
    else:
        return PlanValidationOutcome.FAIL, _format_validation_error(
            "Validation code must define a callable 'validate(value, **context)'",
            origin,
            context,
        )

    outcome, outcome_message = _coerce_validation_outcome(
        raw_result,
        allow_warn=allow_warn,
    )
    if outcome == PlanValidationOutcome.PASS:
        return PlanValidationOutcome.PASS, ""

    fallback_message = (
        "Validation warning"
        if outcome == PlanValidationOutcome.WARN
        else "Validation failed"
    )
    return outcome, _format_validation_error(
        outcome_message or fallback_message,
        origin,
        context,
        value_summary=value_summary,
    )


def validate_value(
    spec: ValidationSpec,
    value: Any,
    *,
    base_path: Path | None = None,
    context: dict[str, Any] | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> tuple[InputValidationOutcome, str]:
    """Evaluate a Python validation rule against a value.

    The validation code must define a callable `validate(value, **context)` that
    returns either `"pass"` or `"fail"`, or a dict that includes a `"status"`
    key with one of those values. The resolved `context` dict is forwarded as
    keyword arguments.

    Args:
        spec: Validation rule with inline code or a local script reference.
        value: The value to validate.
        base_path: Base directory used to resolve relative script paths.
        context: Additional names forwarded as keyword arguments to `validate`.
        cache_dir: Cache directory for fetching remote scripts.
        auth_config: Optional host-keyed auth configuration for remote fetching.

    Returns:
        Tuple of (`InputValidationOutcome`, error_message).
        error_message is empty when the outcome is `PASS`.
    """
    outcome, message = validate_value_with_outcome(
        spec,
        value,
        base_path=base_path,
        context=context,
        cache_dir=cache_dir,
        auth_config=auth_config,
        allow_warn=False,
    )
    if outcome == PlanValidationOutcome.PASS:
        return InputValidationOutcome.PASS, ""

    return InputValidationOutcome.FAIL, message


def evaluate_plan_validations_with_results(
    plan_validations: dict[str, PlanValidation],
    plan_data: dict[str, Any],
    *,
    base_path: Path | None = None,
    context: dict[str, Any] | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> list[PlanValidationResult]:
    """Evaluate post-plan validations and return structured outcomes.

    Args:
        plan_validations: Named plan validation rules from tool config.
        plan_data: Parsed OpenTofu JSON plan document.
        base_path: Base directory used to resolve relative script paths.
        context: Additional names exposed to each validation rule.
        cache_dir: Cache directory for fetching remote scripts.
        auth_config: Optional host-keyed auth configuration for remote fetching.

    Returns:
        List of structured outcomes for each enabled plan validation rule.
    """
    results: list[PlanValidationResult] = []
    resource_summary = _summarize_plan_resources(plan_data)
    validation_context = {"kind": "plan_validation", **(context or {})}
    stack_name = validation_context.get("stack_name", "<unknown>")
    enabled_rules = [
        name
        for name, plan_validation in plan_validations.items()
        if plan_validation.enabled
    ]

    if enabled_rules:
        LOGGER.info(
            "Evaluating plan validations for stack: {stack_name}",
            stack_name=stack_name,
        )
    else:
        LOGGER.info(
            "No enabled plan validations for stack: {stack_name}",
            stack_name=stack_name,
        )

    concurrency = _resolve_plan_validation_concurrency()
    if concurrency <= 1 or len(enabled_rules) <= 1:
        for name, plan_validation in plan_validations.items():
            if not plan_validation.enabled:
                continue
            results.append(
                _process_plan_validation(
                    name,
                    plan_validation,
                    plan_data,
                    base_path=base_path,
                    context=validation_context,
                    cache_dir=cache_dir,
                    auth_config=auth_config,
                    resource_summary=resource_summary,
                    stack_name=stack_name,
                )
            )
    else:
        semaphore = asyncio.Semaphore(concurrency)

        async def _run_all() -> list[PlanValidationResult]:
            tasks = [
                asyncio.create_task(
                    _evaluate_plan_validation_task(
                        name,
                        plan_validation,
                        plan_data,
                        base_path=base_path,
                        context=validation_context,
                        cache_dir=cache_dir,
                        auth_config=auth_config,
                        resource_summary=resource_summary,
                        stack_name=stack_name,
                        semaphore=semaphore,
                    )
                )
                for name, plan_validation in plan_validations.items()
                if plan_validation.enabled
            ]
            return await asyncio.gather(*tasks)

        results = asyncio.run(_run_all())

    if enabled_rules and all(
        result.status == PlanValidationOutcome.PASS for result in results
    ):
        LOGGER.info(
            "Plan validations passed for stack: {stack_name}",
            stack_name=stack_name,
        )

    return results


def evaluate_plan_validations(
    plan_validations: dict[str, PlanValidation],
    plan_data: dict[str, Any],
    *,
    base_path: Path | None = None,
    context: dict[str, Any] | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> list[str]:
    """Evaluate post-plan validations against parsed plan JSON.

    Args:
        plan_validations: Named plan validation rules from tool config.
        plan_data: Parsed OpenTofu JSON plan document.
        base_path: Base directory used to resolve relative script paths.
        context: Additional names exposed to each validation rule.
        cache_dir: Cache directory for fetching remote scripts.
        auth_config: Optional host-keyed auth configuration for remote fetching.

    Returns:
        List of human-readable validation failures. Empty when all pass.
    """
    failures: list[str] = []
    for result in evaluate_plan_validations_with_results(
        plan_validations,
        plan_data,
        base_path=base_path,
        context=context,
        cache_dir=cache_dir,
        auth_config=auth_config,
    ):
        if result.status != PlanValidationOutcome.FAIL:
            continue

        stack_info = f" for stack '{result.stack_name}'" if result.stack_name else ""
        failures.append(
            f"Plan validation '{result.name}' failed{stack_info}: {result.message}"
        )

    return failures


def apply_transform(
    spec: TransformSpec,
    value: Any,
    *,
    base_path: Path | None = None,
    context: dict[str, Any] | None = None,
    cache_dir: Path | None = None,
    auth_config: RemoteAuthConfig | None = None,
) -> Any:
    """Apply a Python transform rule to a value.

    The transform code must define a callable `transform(value, **context)`.
    The resolved `context` dict is forwarded as keyword arguments.

    Args:
        spec: Transform rule with inline code or a local script reference.
        value: The value to transform.
        base_path: Base directory used to resolve relative script paths.
        context: Additional names exposed to the transform code.
        cache_dir: Cache directory for fetching remote scripts.
        auth_config: Optional host-keyed auth configuration for remote fetching.

    Returns:
        The value returned by `transform(value, **context)`.

    Raises:
        StacksmithTransformError: If transform code cannot be loaded or executed.
    """
    try:
        code, origin = _load_code(
            spec,
            base_path,
            cache_dir=cache_dir,
            auth_config=auth_config,
        )
        code = textwrap.dedent(code)
    except (StacksmithError, OSError) as exc:
        raise StacksmithTransformError(f"Failed to load transform code: {exc}") from exc

    ns: dict[str, Any] = {}
    try:
        exec(compile(code, origin, "exec"), ns)  # noqa: S102
    except Exception as exc:
        message = str(exc) if str(exc) else type(exc).__name__
        raise StacksmithTransformError(
            f"Transform code execution failed: {message}"
        ) from exc

    transform_fn = ns.get("transform")
    if not callable(transform_fn):
        raise StacksmithTransformError(
            "Transform code must define a callable 'transform(value, **context)'"
        )

    try:
        return transform_fn(value, **(context or {}))
    except Exception as exc:
        message = str(exc) if str(exc) else type(exc).__name__
        raise StacksmithTransformError(f"Transform function failed: {message}") from exc
