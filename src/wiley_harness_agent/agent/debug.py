"""Debug trace recorder: JSONL log of LLM requests, stream events, and tool runs."""

import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class DebugRecorder:
    """Append agent-execution trace records to a per-session JSONL file.

    每条记录写一行 JSON 后 flush 但不 fsync：response_event 是逐事件级频率，
    逐条 fsync 代价过高；flush 足以在应用崩溃后保留已写内容。
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        self.path = path

    def record_session_start(
        self,
        *,
        session_id: str,
        model: str,
        base_url: str,
        max_tokens: int,
        thinking_budget_tokens: int,
    ) -> None:
        self._append(
            "session_start",
            session_id=session_id,
            model=model,
            base_url=base_url,
            max_tokens=max_tokens,
            thinking_budget_tokens=thinking_budget_tokens,
        )

    def record_request(
        self, *, turn: int, round_index: int, body: Mapping[str, Any]
    ) -> None:
        self._append("request", turn=turn, round=round_index, body=dict(body))

    def record_response_event(
        self, *, turn: int, round_index: int, event: Any
    ) -> None:
        if dataclasses.is_dataclass(event) and not isinstance(event, type):
            payload: Any = dataclasses.asdict(event)
        else:
            payload = {"value": str(event)}
        self._append("response_event", turn=turn, round=round_index, event=payload)

    def record_response_end(
        self,
        *,
        turn: int,
        round_index: int,
        stop_reason: str | None,
        usage: Mapping[str, Any],
    ) -> None:
        self._append(
            "response_end",
            turn=turn,
            round=round_index,
            stop_reason=stop_reason,
            usage=dict(usage),
        )

    def record_tool_call(
        self,
        *,
        turn: int,
        round_index: int,
        tool_name: str,
        tool_call_id: str,
        arguments: Any,
    ) -> None:
        self._append(
            "tool_call",
            turn=turn,
            round=round_index,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            arguments=arguments,
        )

    def record_tool_result(
        self,
        *,
        turn: int,
        round_index: int,
        tool_name: str,
        tool_call_id: str,
        output: Any,
    ) -> None:
        self._append(
            "tool_result",
            turn=turn,
            round=round_index,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            output=output,
        )

    def record_error(
        self, *, turn: int, round_index: int, error_type: str, message: str
    ) -> None:
        self._append(
            "error",
            turn=turn,
            round=round_index,
            error_type=error_type,
            message=message,
        )

    def _append(self, record_type: str, **payload: Any) -> None:
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": record_type,
            **payload,
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            file.flush()
