"""Write tool: create or fully overwrite a file, modeled on Claude Code's
FileWriteTool.

Content is written exactly as provided — line endings in ``content`` are
intentional and are not rewritten. Parent directories are created as needed.
"""

from pathlib import Path

from wy_core import ApprovalRequest, Tool

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

    def approve(self, input: dict, workspace: Path) -> ApprovalRequest | None:
        """工作区内文件直接放行，工作区外需审批。"""
        path = self._resolve_for_approval(input.get("file_path"))
        if path is not None and path.is_relative_to(workspace):
            return None  # 工作区内直接放行
        display = str(path) if path else str(input.get("file_path", "(未知)"))
        return ApprovalRequest(
            heading="写入文件",
            question="是否写入该文件？",
            fields=[("文件", display)],
            key=f"write:{path}" if path else None,
        )

    def _resolve_for_approval(self, raw: object) -> Path | None:
        """从 raw input 中提取路径并规范化为绝对路径，失败返回 None。"""
        if not raw:
            return None
        try:
            return resolve_path(raw).resolve()
        except FileToolError:
            return None

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
