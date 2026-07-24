"""Tool abstraction callable by the agent, plus built-in tools."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from wiley_harness_agent.agent.text_editor import TEXT_EDITOR_TOOL, execute_text_editor


@dataclass(frozen=True, slots=True)
class Tool:
    """A model-callable tool: its API schema plus the local executor."""

    definition: Mapping[str, Any]
    execute: Callable[[Mapping[str, Any]], str]

    @property
    def name(self) -> str:
        return str(self.definition["name"])


TEXT_EDITOR = Tool(definition=TEXT_EDITOR_TOOL, execute=execute_text_editor)

DEFAULT_TOOLS: tuple[Tool, ...] = (TEXT_EDITOR,)
