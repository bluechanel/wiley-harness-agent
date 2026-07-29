"""Read tool: return file contents with line numbers, modeled on Claude Code's
FileReadTool.

复刻自 wy-coding-agent 的 ``tools/read.py``,并把其 ``tools/files.py`` 中
本工具用到的辅助(错误类型、路径解析、换行规范化、missing-file 提示)内联
进来——本包只有 read 一个文件工具,没有共享需求。两侧修改需互相评估。

Capabilities ported from the reference: cat -n style output with 1-based line
numbers, offset/limit windowing with a default line cap, per-line truncation,
size guards, empty-file and short-file warnings, binary-extension and device-
file rejection, and "did you mean" suggestions for missing files.
"""

import os
from pathlib import Path
from typing import Any

from wy_core import Tool

_DEFAULT_LINE_LIMIT = 2000
_MAX_LINE_CHARS = 2000
_MAX_FULL_READ_BYTES = 256 * 1024  # full reads above this must use offset/limit
_MAX_READ_BYTES = 50 * 1024 * 1024  # absolute guard, even with offset/limit

# Device files that would hang the process: infinite output or blocking input.
# Checked by path only (no I/O). Safe devices like /dev/null are omitted.
_BLOCKED_DEVICE_PATHS = {
    "/dev/zero",
    "/dev/random",
    "/dev/urandom",
    "/dev/full",
    "/dev/stdin",
    "/dev/tty",
    "/dev/console",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/fd/0",
    "/dev/fd/1",
    "/dev/fd/2",
}

_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".icns",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".class", ".pyc", ".wasm",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac",
    ".sqlite", ".db", ".parquet", ".pickle", ".pkl",
}  # fmt: skip


class FileToolError(ValueError):
    """Raised when a file tool request is invalid or cannot be applied."""


def _normalize(text: str) -> str:
    """CRLF-normalize content so windowing and truncation see one form."""
    return text.replace("\r\n", "\n")


def _resolve_path(raw: Any, argument: str = "file_path") -> Path:
    if not isinstance(raw, str) or not raw:
        raise FileToolError(f"Missing required argument: {argument}")
    return Path(os.path.abspath(Path(raw).expanduser()))


def _find_similar_file(path: Path) -> str | None:
    """Suggest a same-named file with a different extension, if one exists."""
    try:
        siblings = list(path.parent.iterdir())
    except OSError:
        return None
    for sibling in sorted(siblings):
        if sibling.stem == path.stem and sibling != path and sibling.is_file():
            return str(sibling)
    return None


def _missing_file_error(path: Path) -> FileToolError:
    message = f"File does not exist. Current working directory is {Path.cwd()}."
    similar = _find_similar_file(path)
    if similar:
        message += f" Did you mean {similar}?"
    return FileToolError(message)


def _is_blocked_device(path: str) -> bool:
    if path in _BLOCKED_DEVICE_PATHS:
        return True
    # /proc/<pid>/fd/0-2 are Linux aliases for stdio
    return path.startswith("/proc/") and path.endswith(("/fd/0", "/fd/1", "/fd/2"))


def _format_lines(lines: list[str], start_line: int) -> str:
    numbered = []
    for index, text in enumerate(lines, start=start_line):
        if len(text) > _MAX_LINE_CHARS:
            text = text[:_MAX_LINE_CHARS] + "… (line truncated)"
        numbered.append(f"{index:>6}\t{text}")
    return "\n".join(numbered)


class ReadTool(Tool):
    name = "read"
    description = (
        "Reads a file from the local filesystem.\n"
        "- file_path may be absolute or relative to the current working "
        "directory\n"
        "- By default reads up to 2000 lines from the beginning; use offset "
        "and limit for longer files\n"
        "- Lines longer than 2000 characters are truncated\n"
        "- Results use cat -n format: line numbers, then a tab, then content. "
        "Never include that prefix when quoting file content\n"
        "- Cannot read binary files (images, archives, executables)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "The path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "The line number to start reading from. Only provide if "
                    "the file is too large to read at once."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "The number of lines to read. Only provide if the file "
                    "is too large to read at once."
                ),
            },
        },
        "required": ["file_path"],
        "additionalProperties": False,
    }

    def execute(self, input: dict) -> str:
        path = _resolve_path(input.get("file_path"))
        offset = input.get("offset")
        limit = input.get("limit")
        if offset is not None and (
            isinstance(offset, bool) or not isinstance(offset, int) or offset < 1
        ):
            raise FileToolError("offset must be a positive integer (1-based line number)")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise FileToolError("limit must be a positive integer")
        start_line = offset or 1

        path_text = str(path)
        if _is_blocked_device(path_text):
            raise FileToolError(
                f"Cannot read '{path_text}': this device file would block or "
                "produce infinite output."
            )
        if path.suffix.lower() in _BINARY_EXTENSIONS:
            raise FileToolError(
                "This tool cannot read binary files. The file appears to be a "
                f"binary {path.suffix} file."
            )

        try:
            stat = path.stat()
        except FileNotFoundError:
            raise _missing_file_error(path) from None
        if path.is_dir():
            raise FileToolError(f"Path is a directory, not a file: {path_text}")

        if stat.st_size > _MAX_READ_BYTES:
            raise FileToolError(
                f"File is too large to read ({stat.st_size} bytes; maximum is "
                f"{_MAX_READ_BYTES})."
            )
        if limit is None and offset is None and stat.st_size > _MAX_FULL_READ_BYTES:
            raise FileToolError(
                f"File content ({stat.st_size} bytes) exceeds maximum allowed "
                f"size for a full read ({_MAX_FULL_READ_BYTES} bytes). Use offset "
                "and limit parameters to read specific portions of the file."
            )

        content = _normalize(path.read_bytes().decode("utf-8", errors="replace"))
        lines = content.splitlines()
        total_lines = len(lines)
        effective_limit = limit if limit is not None else _DEFAULT_LINE_LIMIT
        selected = lines[start_line - 1 : start_line - 1 + effective_limit]
        end_line = start_line - 1 + len(selected)

        if not selected:
            if total_lines == 0:
                return (
                    "<system-reminder>Warning: the file exists but the contents "
                    "are empty.</system-reminder>"
                )
            return (
                "<system-reminder>Warning: the file exists but is shorter than "
                f"the provided offset ({start_line}). The file has {total_lines} "
                "lines.</system-reminder>"
            )

        output = _format_lines(selected, start_line)
        if end_line < total_lines:
            output += (
                f"\n\n(File has more lines. Showing lines {start_line}-{end_line} "
                f"of {total_lines}. Use offset: {end_line + 1} to continue.)"
            )
        return output


READ = ReadTool()
