"""Write tool: create or fully overwrite a file, modeled on Claude Code's
FileWriteTool.

Content is written exactly as provided — line endings in ``content`` are
intentional and are not rewritten. Parent directories are created as needed.
"""

from wy_core import Tool

from wy_coding_agent.tools.files import FileToolError, resolve_path


class WriteTool(Tool):
    name = "write"
    description = (
        "Writes a file to the local filesystem, overwriting it if it exists.\n"
        "- Prefer the edit tool for modifying existing files; use write for "
        "new files or full rewrites\n"
        "- Content is written exactly as provided; never include the "
        "line-number prefix from read output"
    )
    parameters = {
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
    }

    def execute(self, input: dict) -> str:
        path = resolve_path(input.get("file_path"))
        content = input.get("content")
        if not isinstance(content, str):
            raise FileToolError("Missing required argument: content")

        path_text = str(path)
        if path.is_dir():
            raise FileToolError(f"Path is a directory, not a file: {path_text}")

        exists = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        # newline="" keeps the content's own line endings — they are intentional.
        with open(path_text, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)

        if exists:
            return f"The file {path_text} has been updated successfully."
        return f"File created successfully at: {path_text}"


WRITE = WriteTool()
