"""Edit tool: exact string replacement in a file, modeled on Claude Code's
FileEditTool.

Capabilities ported from the reference: old_string uniqueness with
``replace_all``, new-file creation via an empty ``old_string``, CRLF
preservation, and the reference's error messages so the model can self-correct.
"""

from wy_core import Tool

from wy_coding_agent.tools.files import (
    FileToolError,
    missing_file_error,
    normalize,
    resolve_path,
)


def write_preserving_crlf(path: str, content: str, had_crlf: bool) -> None:
    """Write normalized content, restoring CRLF endings when the file used them."""
    if had_crlf:
        content = content.replace("\n", "\r\n")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


class EditTool(Tool):
    name = "edit"
    description = (
        "Performs exact string replacement in a file.\n"
        "- Read the file first: old_string must match the file content "
        "exactly, including whitespace and indentation; never include the "
        "line-number prefix from read output\n"
        "- The edit fails if old_string is not unique in the file; either "
        "provide more surrounding context to make it unique, or set "
        "replace_all to true\n"
        "- replace_all replaces every occurrence (useful for renaming)"
    )
    parameters = {
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
    }

    def execute(self, input: dict) -> str:
        path = resolve_path(input.get("file_path"))
        old_string = input.get("old_string")
        new_string = input.get("new_string")
        if not isinstance(old_string, str):
            raise FileToolError("Missing required argument: old_string")
        if not isinstance(new_string, str):
            raise FileToolError("Missing required argument: new_string")
        replace_all = input.get("replace_all", False)
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
            write_preserving_crlf(path_text, normalize(new_string), had_crlf)
            return f"The file {path_text} has been updated successfully."

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

        new_norm = normalize(new_string)
        updated = (
            content.replace(old_string, new_norm)
            if replace_all
            else content.replace(old_string, new_norm, 1)
        )
        write_preserving_crlf(path_text, updated, had_crlf)

        if replace_all:
            return (
                f"The file {path_text} has been updated. All {matches} "
                "occurrences were successfully replaced."
            )
        return f"The file {path_text} has been updated successfully."


EDIT = EditTool()
