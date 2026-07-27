"""Write tool: create or fully overwrite a file, modeled on Claude Code's
FileWriteTool.

Capabilities ported from the reference: overwriting an existing file requires
that it was read in this session and has not changed on disk since (shared
read-state registry, with the same content-compare fallback for timestamp
churn); new files are created freely with parent directories. Content is
written exactly as provided — line endings in ``content`` are intentional and
are not rewritten.
"""

from typing import Any, Mapping

from wy_coding_agent.tools.base import FunctionTool
from wy_coding_agent.tools.file_state import (
    FileToolError,
    ensure_safe_to_modify,
    normalize,
    record_write,
)
from wy_coding_agent.tools.read import resolve_path

WRITE_TOOL: dict[str, Any] = {
    "name": "write",
    "description": (
        "Writes a file to the local filesystem, overwriting it if it exists.\n"
        "- If the file exists, you must use the read tool on it in this "
        "session first, or the write will fail\n"
        "- Prefer the edit tool for modifying existing files; use write for "
        "new files or full rewrites\n"
        "- Content is written exactly as provided; never include the "
        "line-number prefix from read output"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The path to the file to write.",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file.",
            },
        },
        "required": ["file_path", "content"],
        "additionalProperties": False,
    },
}


def execute_write(arguments: Mapping[str, Any]) -> str:
    """Execute a write request and return a confirmation message."""
    path = resolve_path(arguments.get("file_path"))
    content = arguments.get("content")
    if not isinstance(content, str):
        raise FileToolError("Missing required argument: content")

    path_text = str(path)
    if path.is_dir():
        raise FileToolError(f"Path is a directory, not a file: {path_text}")

    exists = path.exists()
    if exists:
        ensure_safe_to_modify(path_text)

    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" keeps the content's own line endings — they are intentional.
    with open(path_text, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)
    record_write(path_text, normalize(content))

    if exists:
        return f"The file {path_text} has been updated successfully."
    return f"File created successfully at: {path_text}"


WRITE = FunctionTool(definition=WRITE_TOOL, execute=execute_write)
