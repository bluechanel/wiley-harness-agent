import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping
from uuid import UUID, uuid4

from wiley_agent.usage import ChatUsage


SessionRole = Literal["assistant", "user", "tool_call", "tool_output"]


class SessionError(RuntimeError):
    """Raised when a session cannot be created or restored."""


@dataclass(frozen=True, slots=True)
class SessionRecord:
    timestamp: str
    session_id: str
    role: SessionRole
    content: Any
    kind: str | None = None
    usage: ChatUsage | None = None
    total_usage: ChatUsage | None = None
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
            usage=(ChatUsage.from_mapping(usage_value) if usage_value else None),
            total_usage=(
                ChatUsage.from_mapping(total_usage_value)
                if total_usage_value
                else None
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
        sessions_dir = sessions_dir if sessions_dir is not None else Path.cwd() / "sessions"
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
    def total_usage(self) -> ChatUsage:
        for record in reversed(self._records):
            if record.role == "assistant" and record.kind == "answer":
                if record.total_usage:
                    return record.total_usage

        total = ChatUsage()
        for record in self._records:
            if record.role == "assistant" and record.kind == "answer":
                total = total.add(record.usage or ChatUsage())
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

    def conversation_messages(self) -> list[dict[str, str]]:
        """Restore only completed user/assistant turns for the Anthropic API."""
        messages: list[dict[str, str]] = []
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
                messages.extend(
                    [
                        {"role": "user", "content": pending_user},
                        {"role": "assistant", "content": record.content},
                    ]
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
        usage: ChatUsage,
        total_usage: ChatUsage,
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
        usage: ChatUsage | None = None,
        total_usage: ChatUsage | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> SessionRecord:
        record = SessionRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
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
            payload["usage"] = usage.to_dict()
        if total_usage is not None:
            payload["total_usage"] = total_usage.to_dict()
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
