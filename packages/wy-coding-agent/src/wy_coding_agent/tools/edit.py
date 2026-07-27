"""Edit tool: exact string replacement in a file, modeled on Claude Code's
FileEditTool.

Capabilities ported from the reference: read-before-edit enforcement and
staleness detection (via the shared read-state registry, with a content-
compare fallback for timestamp churn), old_string uniqueness with
``replace_all``, new-file creation via an empty ``old_string``, CRLF
preservation, and the reference's error messages so the model can self-correct.
"""

from typing import Any, Mapping

from wy_coding_agent.tools.base import FunctionTool
from wy_coding_agent.tools.file_state import (
    FileToolError,
    ensure_safe_to_modify,
    normalize,
    record_write,
)
from wy_coding_agent.tools.read import missing_file_error, resolve_path

EDIT_TOOL: dict[str, Any] = {
    "name": "edit",
    "description": (
        "Performs exact string replacement in a file.\n"
        "- You must use the read tool on the file in this session before "
        "editing, or the edit will fail\n"
        "- old_string must match the file content exactly, including "
        "whitespace and indentation; never include the line-number prefix "
        "from read output\n"
        "- The edit fails if old_string is not unique in the file; either "
        "provide more surrounding context to make it unique, or set "
        "replace_all to true\n"
        "- replace_all replaces every occurrence (useful for renaming)"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The path to the file to modify.",
            },
            "old_string": {
                "type": "string",
                "description": "The text to replace.",
            },
            "new_string": {
                "type": "string",
                "description": (
                    "The text to replace it with (must be different from "
                    "old_string)."
                ),
            },
            "replace_all": {
                "type": "boolean",
                "description": (
                    "Replace all occurrences of old_string (default false)."
                ),
            },
        },
        "required": ["file_path", "old_string", "new_string"],
        "additionalProperties": False,
    },
}


def write_preserving_crlf(path: str, content: str, had_crlf: bool) -> None:
    """Write normalized content, restoring CRLF endings when the file used them."""
    if had_crlf:
        content = content.replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def execute_edit(arguments: Mapping[str, Any]) -> str:
    """Execute an edit request and return a confirmation message."""
    path = resolve_path(arguments.get("file_path"))
    old_string = arguments.get("old_string")
    new_string = arguments.get("new_string")
    if not isinstance(old_string, str):
        raise FileToolError("Missing required argument: old_string")
    if not isinstance(new_string, str):
        raise FileToolError("Missing required argument: new_string")
    replace_all = arguments.get("replace_all", False)
    if not isinstance(replace_all, bool):
        raise FileToolError("replace_all must be a boolean")

    if old_string == new_string:
        raise FileToolError(
            "No changes to make: old_string and new_string are exactly the same."
        )

    path_text = str(path)
    try:
        raw = path.read_bytes().decode("utf-8", errors="replace")
    except FileNotFoundError:
        # Empty old_string on a nonexistent file means new-file creation.
        if old_string == "":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_string, encoding="utf-8", newline="")
            record_write(path_text, normalize(new_string))
            return f"File created successfully at: {path_text}"
        raise missing_file_error(path) from None
    except IsADirectoryError:
        raise FileToolError(f"Path is a directory, not a file: {path_text}") from None

    content = normalize(raw)
    had_crlf = "\r\n" in raw

    if old_string == "":
        # File exists: an empty old_string is only valid on an empty file.
        if content.strip() != "":
            raise FileToolError("Cannot create new file - file already exists.")
        ensure_safe_to_modify(path_text)
        write_preserving_crlf(path_text, normalize(new_string), had_crlf)
        record_write(path_text, normalize(new_string))
        return f"The file {path_text} has been updated successfully."

    ensure_safe_to_modify(path_text)

    matches = content.count(old_string)
    if matches == 0:
        raise FileToolError(
            f"String to replace not found in file.\nString: {old_string}"
        )
    if matches > 1 and not replace_all:
        raise FileToolError(
            f"Found {matches} matches of the string to replace, but "
            "replace_all is false. To replace all occurrences, set "
            "replace_all to true. To replace only one occurrence, please "
            "provide more context to uniquely identify the instance.\n"
            f"String: {old_string}"
        )

    old_norm = old_string
    new_norm = normalize(new_string)
    updated = (
        content.replace(old_norm, new_norm)
        if replace_all
        else content.replace(old_norm, new_norm, 1)
    )
    write_preserving_crlf(path_text, updated, had_crlf)
    record_write(path_text, updated)

    if replace_all:
        return (
            f"The file {path_text} has been updated. All {matches} "
            "occurrences were successfully replaced."
        )
    return f"The file {path_text} has been updated successfully."


EDIT = FunctionTool(definition=EDIT_TOOL, execute=execute_edit)
