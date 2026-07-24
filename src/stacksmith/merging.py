from copy import deepcopy
from typing import Any, Literal, Sequence, TypeAlias

import jmespath
from deepmerge import Merger
from jmespath import exceptions as jmespath_exceptions

from .enums import MergeMode
from .exceptions import StacksmithConfigError
from .models import MergeConfig, MergePolicy

MergeScope: TypeAlias = Literal["stack", "config", "runfile", "vars"]


def _normalize_path(path: Sequence[Any]) -> list[str | int]:
    return [
        segment if isinstance(segment, (str, int)) else str(segment) for segment in path
    ]


def _json_pointer(path: Sequence[Any]) -> str:
    return "".join(
        f"/{str(segment).replace('~', '~0').replace('/', '~1')}" for segment in path
    )


class AddressAwareMerger(Merger):
    """Apply deep or override strategies according to the current node address."""

    def __init__(self, merge_config: MergeConfig, scope: MergeScope) -> None:
        super().__init__(
            [(dict, ["merge"]), (list, ["append"]), (set, ["union"])],
            ["override"],
            ["override"],
        )
        self._policy = (
            merge_config
            if isinstance(merge_config, MergePolicy)
            else MergePolicy(default=MergeMode(merge_config))
        )
        self._scope = scope
        self._compiled_rules = [
            (rule, jmespath.compile(rule.select)) for rule in self._policy.rules
        ]
        self.replaced_paths: list[tuple[Any, ...]] = []

    def value_strategy(self, path: list, base: Any, nxt: Any) -> Any:
        """Merge one node using the strategy selected for its address.

        Args:
            path: Address segments for the node being merged.
            base: Value accumulated from earlier layers.
            nxt: Value supplied by the incoming layer.

        Returns:
            Merged or replaced value.

        Raises:
            StacksmithConfigError: If a selector fails or is not boolean.
        """
        if self._mode_for(path) == MergeMode.OVERRIDE:
            self.replaced_paths.append(tuple(path))
            return deepcopy(nxt)

        if not isinstance(base, (dict, list, set)) or not isinstance(nxt, type(base)):
            self.replaced_paths.append(tuple(path))
        return super().value_strategy(path, base, nxt)

    def _mode_for(self, path: Sequence[Any]) -> MergeMode:
        mode = self._policy.default
        context = {
            "scope": self._scope,
            "address": _json_pointer(path),
            "path": _normalize_path(path),
        }
        for rule, expression in self._compiled_rules:
            try:
                matches = expression.search(context)
            except jmespath_exceptions.JMESPathError as exc:
                raise StacksmithConfigError(
                    f"Merge rule selector failed at '{context['address']}': {exc}"
                ) from exc
            if not isinstance(matches, bool):
                raise StacksmithConfigError(
                    "Merge rule selector must evaluate to a boolean value. "
                    f"Selector {rule.select!r} produced {type(matches).__name__} "
                    f"at '{context['address']}'."
                )
            if matches:
                mode = rule.mode
        return mode
