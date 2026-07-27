"""Shared read-state registry for the file tools (read / edit / write).

Ports the ``readFileState`` mechanism of Claude Code's file tools: ``read``
records what it saw (content, mtime, range); ``edit`` and ``write`` refuse to
touch a file that has not been read in this process, or that changed on disk
after the last read. A successful edit/write refreshes the record so chained
edits do not require re-reading. State is process-level, matching the bash
tool's session singleton.
"""

import os
from dataclasses import dataclass

_MTIME_EPSILON = 1e-6  # float st_mtime comparisons need a tolerance


class FileToolError(ValueError):
    """Raised when a file tool request is invalid or cannot be applied."""


@dataclass(frozen=True, slots=True)
class ReadRecord:
    """What the model last saw of a file."""

    content: str  # CRLF-normalized content of the range that was read
    timestamp: float  # file mtime when the content was captured
    offset: int | None  # requested range; None when recorded by edit/write
    limit: int | None
    partial: bool  # the read was cut off by a size cap, not by request
    full: bool  # content covers the whole file (enables content fallback)


_records: dict[str, ReadRecord] = {}


def get_record(path: str) -> ReadRecord | None:
    return _records.get(path)


def record_read(
    path: str, content: str, *, offset: int, limit: int | None, partial: bool, full: bool
) -> None:
    _records[path] = ReadRecord(
        content=content,
        timestamp=os.stat(path).st_mtime,
        offset=offset,
        limit=limit,
        partial=partial,
        full=full,
    )


def record_write(path: str, content: str) -> None:
    """Refresh state after edit/write so chained edits skip re-reading."""
    _records[path] = ReadRecord(
        content=content,
        timestamp=os.stat(path).st_mtime,
        offset=None,  # None keeps read-dedup from matching post-write state
        limit=None,
        partial=False,
        full=True,
    )


def reset_state() -> None:
    """Forget all read state (used by tests)."""
    _records.clear()


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n")


def read_normalized(path: str) -> str:
    with open(path, "rb") as handle:
        return normalize(handle.read().decode("utf-8", errors="replace"))


def ensure_safe_to_modify(path: str) -> None:
    """Gate edit/write on an existing file: must be read, and not stale.

    Mirrors the reference tools: a stale mtime alone is not fatal when the last
    read covered the whole file and the on-disk content is byte-identical
    (timestamps can change without content changes).
    """
    record = _records.get(path)
    if record is None or record.partial:
        raise FileToolError(
            "File has not been read yet. Read it first before writing to it."
        )
    if os.stat(path).st_mtime > record.timestamp + _MTIME_EPSILON:
        if record.full and read_normalized(path) == record.content:
            return  # timestamp churn without a content change
        raise FileToolError(
            "File has been modified since read, either by the user or by a "
            "linter. Read it again before attempting to write it."
        )
