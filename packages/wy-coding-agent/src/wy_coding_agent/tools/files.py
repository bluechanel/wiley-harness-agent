"""文件三件套(read/edit/write)的共享辅助:错误类型、路径解析与换行规范化。"""

import os
from pathlib import Path
from typing import Any


class FileToolError(ValueError):
    """Raised when a file tool request is invalid or cannot be applied."""


def normalize(text: str) -> str:
    """CRLF-normalize content so matching and editing see one line-ending form."""
    return text.replace("\r\n", "\n")


def resolve_path(raw: Any, argument: str = "file_path") -> Path:
    """Shared path handling for the file tools."""
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


def missing_file_error(path: Path) -> FileToolError:
    message = f"File does not exist. Current working directory is {Path.cwd()}."
    similar = _find_similar_file(path)
    if similar:
        message += f" Did you mean {similar}?"
    return FileToolError(message)
