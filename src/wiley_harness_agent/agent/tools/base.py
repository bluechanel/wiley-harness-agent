"""Tool abstraction callable by the agent."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Tool:
    """A model-callable tool: its API schema plus the local executor."""

    definition: Mapping[str, Any]
    execute: Callable[[Mapping[str, Any]], str]

    @property
    def name(self) -> str:
        return str(self.definition["name"])
