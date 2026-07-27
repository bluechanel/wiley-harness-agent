"""持久会话:JSONL 记录追加与恢复。

文件格式与旧 wiley-agent 会话完全兼容:usage 序列化沿用
``cache_creation_input_tokens``/``cache_read_input_tokens`` 键名,对应
``wy_core.Usage`` 的 ``cache_write_tokens``/``cache_read_tokens``。
"""

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import UUID, uuid4

from wy_core import Message, TextBlock, Usage, user_message

SessionRole = Literal["assistant", "user", "tool_call", "tool_output"]


class SessionError(RuntimeError):
    """Raised when a session cannot be created or restored."""


def usage_to_dict(usage: Usage) -> dict[str, int]:
    """序列化为旧文件格式的键名,保持既有会话文件可继续使用。"""
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_input_tokens": usage.cache_write_tokens,
        "cache_read_input_tokens": usage.cache_read_tokens,
    }


def usage_from_mapping(value: Mapping[str, Any] | None) -> Usage:
    if not value:
        return Usage()
    return Usage(
        input_tokens=_as_token_count(value.get("input_tokens")),
        output_tokens=_as_token_count(value.get("output_tokens")),
        cache_write_tokens=_as_token_count(value.get("cache_creation_input_tokens")),
        cache_read_tokens=_as_token_count(value.get("cache_read_input_tokens")),
    )


def _as_token_count(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


@dataclass(frozen=True, slots=True)
class SessionRecord:
    timestamp: str
    session_id: str
    role: SessionRole
    content: Any
    kind: str | None = None
    usage: Usage | None = None
    total_usage: Usage | None = None
    metadata: Mapping[str, Any] | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SessionRecord":
        role = value.get("role")
        if role not in {"assistant", "user", "tool_call", "tool_output"}:
            raise SessionError(f"不支持的会话消息角色：{role!r}")
        usage_value = _as_mapping(value.get("usage"))
        total_usage_value = _as_mapping(value.get("total_usage"))
        return cls(
            timestamp=str(value.get("timestamp", "")),
            session_id=str(value.get("session_id", "")),
            role=role,
            content=value.get("content", ""),
            kind=value.get("kind") if isinstance(value.get("kind"), str) else None,
            usage=(usage_from_mapping(usage_value) if usage_value else None),
            total_usage=(
                usage_from_mapping(total_usage_value) if total_usage_value else None
            ),
            metadata=_as_mapping(value.get("metadata")),
        )


class SessionStore:
    """Append conversation events to JSONL and restore completed turns."""

    def __init__(
        self,
        session_id: str | None = None,
        *,
        sessions_dir: Path | None = None,
    ) -> None:
        sessions_dir = sessions_dir if sessions_dir is not None else Path.cwd() / ".agent_session"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = _normalize_session_id(session_id) if session_id else str(uuid4())
        self.path = sessions_dir / f"{self.session_id}.jsonl"

        if session_id:
            if not self.path.is_file():
                raise SessionError(f"找不到会话文件：{self.path}")
        else:
            self.path.touch(exist_ok=False)

        self._records = self._load_records()

    @property
    def records(self) -> tuple[SessionRecord, ...]:
        return tuple(self._records)

    @property
    def total_usage(self) -> Usage:
        for record in reversed(self._records):
            if record.role == "assistant" and record.kind == "answer":
                if record.total_usage:
                    return record.total_usage

        total = Usage()
        for record in self._records:
            if record.role == "assistant" and record.kind == "answer":
                total.add(record.usage or Usage())
        return total

    @property
    def last_context_tokens(self) -> int:
        """Context size after the latest completed turn.

        旧记录没有 metadata.context_tokens 时退回该轮 usage 的分量和
        （单轮请求下两者一致，含工具的多轮请求会偏大）。
        """
        for record in reversed(self._records):
            if record.role == "assistant" and record.kind == "answer":
                value = (record.metadata or {}).get("context_tokens")
                if isinstance(value, int) and value >= 0:
                    return value
                if record.usage:
                    return record.usage.context_tokens
                return 0
        return 0

    def conversation_messages(self) -> list[Message]:
        """Restore only completed user/assistant turns as wy-core messages."""
        messages: list[Message] = []
        pending_user: str | None = None

        for record in self._records:
            if record.role == "user" and isinstance(record.content, str):
                pending_user = record.content
            elif (
                record.role == "assistant"
                and record.kind == "answer"
                and isinstance(record.content, str)
                and pending_user is not None
            ):
                messages.append(user_message(pending_user))
                messages.append(
                    Message(role="assistant", content=[TextBlock(record.content)])
                )
                pending_user = None

        return messages

    def append_user(self, content: str) -> SessionRecord:
        return self._append(role="user", content=content, kind="input")

    def append_assistant(
        self,
        content: str,
        *,
        kind: Literal["thinking", "answer", "error"],
        usage: Usage,
        total_usage: Usage,
        metadata: Mapping[str, Any] | None = None,
    ) -> SessionRecord:
        return self._append(
            role="assistant",
            content=content,
            kind=kind,
            usage=usage,
            total_usage=total_usage,
            metadata=metadata,
        )

    def append_compaction(self, *, dropped: int, summary: str) -> SessionRecord:
        """记录一次自动上下文压缩(摘要为内容,丢弃条数入 metadata)。"""
        return self._append(
            role="assistant",
            content=summary,
            kind="compaction",
            metadata={"dropped": dropped},
        )

    def append_tool_call(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        arguments: Any,
    ) -> SessionRecord:
        return self._append(
            role="tool_call",
            content=arguments,
            kind="tool_call",
            metadata={"tool_name": tool_name, "tool_call_id": tool_call_id},
        )

    def append_tool_output(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        output: Any,
        is_error: bool = False,
    ) -> SessionRecord:
        metadata: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
        }
        if is_error:
            metadata["is_error"] = True
        return self._append(
            role="tool_output",
            content=output,
            kind="tool_output",
            metadata=metadata,
        )

    def _append(
        self,
        *,
        role: SessionRole,
        content: Any,
        kind: str | None = None,
        usage: Usage | None = None,
        total_usage: Usage | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SessionRecord:
        record = SessionRecord(
            timestamp=datetime.now(UTC).isoformat(),
            session_id=self.session_id,
            role=role,
            content=content,
            kind=kind,
            usage=usage,
            total_usage=total_usage,
            metadata=metadata,
        )
        payload: dict[str, Any] = {
            "timestamp": record.timestamp,
            "session_id": record.session_id,
            "role": record.role,
            "kind": record.kind,
            "content": record.content,
        }
        if usage is not None:
            payload["usage"] = usage_to_dict(usage)
        if total_usage is not None:
            payload["total_usage"] = usage_to_dict(total_usage)
        if metadata:
            payload["metadata"] = dict(metadata)

        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")
            file.flush()
            os.fsync(file.fileno())

        self._records.append(record)
        return record

    def _load_records(self) -> list[SessionRecord]:
        records: list[SessionRecord] = []
        with self.path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SessionError(
                        f"会话文件第 {line_number} 行不是有效 JSON：{exc}"
                    ) from exc
                if not isinstance(payload, dict):
                    raise SessionError(f"会话文件第 {line_number} 行必须是 JSON 对象。")
                record = SessionRecord.from_dict(payload)
                if record.session_id != self.session_id:
                    raise SessionError(
                        f"会话文件第 {line_number} 行的 session_id 不匹配。"
                    )
                records.append(record)
        return records


def _normalize_session_id(value: str) -> str:
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise SessionError(f"无效的 session_id：{value}") from exc


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None
