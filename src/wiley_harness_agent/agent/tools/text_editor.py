"""File editing tool exposed to the agent.

The implementation is deliberately independent from the chat and UI layers so it
can be used directly in tests or by another agent runtime.
"""

from pathlib import Path
from typing import Any, Mapping

from wiley_harness_agent.agent.tools.base import Tool


class TextEditorError(ValueError):
    """Raised when a text editor request is invalid or cannot be applied."""


TEXT_EDITOR_TOOL: dict[str, Any] = {
    "name": "text_editor",
    "description": (
        "View and edit text files. Use one of view, str_replace, create, or insert."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["view", "str_replace", "create", "insert"],
            },
            "path": {"type": "string"},
            "view_range": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
                "minItems": 2,
                "maxItems": 2,
            },
            "old_str": {"type": "string"},
            "new_str": {"type": "string"},
            "file_text": {"type": "string"},
            "insert_line": {"type": "integer", "minimum": 1},
            "insert_text": {"type": "string"},
        },
        "required": ["mode", "path"],
        "additionalProperties": False,
    },
}


def _required(arguments: Mapping[str, Any], name: str) -> Any:
    if name not in arguments:
        raise TextEditorError(f"Missing required argument: {name}")
    return arguments[name]


def execute_text_editor(arguments: Mapping[str, Any]) -> str:
    """Execute a text editor request and return a human-readable result."""
    mode = _required(arguments, "mode")
    path = Path(_required(arguments, "path")).expanduser()

    if mode == "view":
        if not path.is_file():
            raise TextEditorError(f"File does not exist: {path}")
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        view_range = arguments.get("view_range")
        if view_range is not None:
            if len(view_range) != 2 or view_range[0] < 1 or view_range[1] < view_range[0]:
                raise TextEditorError("view_range must be [start, end] using 1-based lines")
            lines = lines[view_range[0] - 1 : view_range[1]]
        return "".join(lines)

    if mode == "create":
        file_text = _required(arguments, "file_text")
        if path.exists():
            raise TextEditorError(f"File already exists: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(file_text, encoding="utf-8")
        return f"Created {path}"

    if mode == "str_replace":
        if not path.is_file():
            raise TextEditorError(f"File does not exist: {path}")
        old_str = _required(arguments, "old_str")
        new_str = _required(arguments, "new_str")
        content = path.read_text(encoding="utf-8")
        occurrences = content.count(old_str)
        if occurrences == 0:
            raise TextEditorError("old_str was not found in the file")
        if occurrences > 1:
            raise TextEditorError("old_str must occur exactly once in the file")
        path.write_text(content.replace(old_str, new_str, 1), encoding="utf-8")
        return f"Updated {path}"

    if mode == "insert":
        if not path.is_file():
            raise TextEditorError(f"File does not exist: {path}")
        line_number = _required(arguments, "insert_line")
        insert_text = _required(arguments, "insert_text")
        if line_number < 1:
            raise TextEditorError("insert_line must be at least 1")
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)
        if line_number > len(lines) + 1:
            raise TextEditorError(f"insert_line must be between 1 and {len(lines) + 1}")
        if insert_text and not insert_text.endswith("\n"):
            insert_text += "\n"
        lines.insert(line_number - 1, insert_text)
        path.write_text("".join(lines), encoding="utf-8")
        return f"Updated {path}"

    raise TextEditorError(f"Unsupported mode: {mode}")


TEXT_EDITOR = Tool(definition=TEXT_EDITOR_TOOL, execute=execute_text_editor)
