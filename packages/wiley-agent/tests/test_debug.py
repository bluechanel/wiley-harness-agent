import asyncio
import json
from pathlib import Path

import pytest

from wiley_agent.debug import DebugRecorder
from wiley_agent.provider import (
    DoneEvent,
    ErrorEvent,
    ProviderError,
    ProviderUsage,
    TextDelta,
    ToolCall,
    UsageEvent,
)
from wiley_agent.service import AgentService
from wiley_agent.tools import Tool


def _read_records(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_debug_recorder_appends_readable_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "trace.debug.jsonl"
    recorder = DebugRecorder(path)
    recorder.record_session_start(
        session_id="sid",
        provider="AnthropicProvider",
        model="m",
    )
    recorder.record_tool_call(
        turn=1,
        round_index=1,
        tool_name="echo",
        tool_call_id="c1",
        arguments={"值": "中文"},
    )
    recorder.record_tool_result(
        turn=1,
        round_index=1,
        tool_name="echo",
        tool_call_id="c1",
        output=Path("/tmp"),
    )

    reopened = DebugRecorder(path)
    reopened.record_error(
        turn=2, round_index=1, error_type="ProviderError", message="boom"
    )

    assert "中文" in path.read_text(encoding="utf-8")
    records = _read_records(path)
    assert [record["type"] for record in records] == [
        "session_start",
        "tool_call",
        "tool_result",
        "error",
    ]
    assert all(record["timestamp"] for record in records)
    assert records[0]["model"] == "m"
    assert records[1]["arguments"] == {"值": "中文"}
    assert records[2]["output"] == "/tmp"
    assert records[3]["turn"] == 2


def test_agent_stream_records_full_tool_round(tmp_path: Path) -> None:
    calls: list[int] = []

    class Provider:
        async def stream_request(self, messages, **options):
            calls.append(len(calls))
            if len(calls) == 1:
                yield ToolCall(
                    index=0,
                    tool_call_id="call-1",
                    name="echo",
                    input_json='{"value": "hi"}',
                )
                yield UsageEvent(ProviderUsage(input_tokens=1), stop_reason="tool_use")
                yield DoneEvent()
            else:
                yield TextDelta("done", index=0)
                yield UsageEvent(ProviderUsage(output_tokens=1), stop_reason="end_turn")
                yield DoneEvent()

    echo = Tool(
        definition={
            "name": "echo",
            "description": "Echo the value back.",
            "input_schema": {"type": "object"},
        },
        execute=lambda arguments: f"echo:{arguments['value']}",
    )
    path = tmp_path / "trace.debug.jsonl"
    agent = AgentService(
        Provider(),  # type: ignore[arg-type]
        instruction="be brief",
        tools=(echo,),
        debug_recorder=DebugRecorder(path),
    )

    async def collect_events():
        return [event async for event in agent.stream("hello")]

    asyncio.run(collect_events())

    records = _read_records(path)
    assert [record["type"] for record in records] == [
        "request",
        "response_event",
        "response_event",
        "response_event",
        "response_end",
        "tool_call",
        "tool_result",
        "request",
        "response_event",
        "response_event",
        "response_event",
        "response_end",
    ]

    first_request = records[0]
    assert first_request["turn"] == 1
    assert first_request["round"] == 1
    body = first_request["body"]
    # 请求体只含 harness 视角的会话状态；厂商参数在 provider 实现内部。
    assert set(body) == {"system", "tools", "messages"}
    assert body["system"] == "be brief"
    assert body["tools"][0]["name"] == "echo"
    assert body["messages"] == [{"role": "user", "content": "hello"}]

    assert records[1]["event"]["kind"] == "tool_call"
    assert records[2]["event"]["kind"] == "usage"
    assert records[3]["event"]["kind"] == "done"

    tool_call = records[5]
    assert tool_call["tool_name"] == "echo"
    assert tool_call["tool_call_id"] == "call-1"
    assert tool_call["arguments"] == {"value": "hi"}
    assert records[6]["output"] == "echo:hi"

    second_request = records[7]
    assert second_request["round"] == 2
    messages = second_request["body"]["messages"]
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"][0]["type"] == "tool_use"
    assert messages[1]["content"][0]["input"] == {"value": "hi"}
    assert messages[2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "call-1",
        "content": "echo:hi",
    }

    ends = [record for record in records if record["type"] == "response_end"]
    assert [end["stop_reason"] for end in ends] == ["tool_use", "end_turn"]
    assert ends[0]["usage"]["input_tokens"] == 1
    assert ends[1]["usage"]["output_tokens"] == 1


def test_agent_stream_records_provider_error(tmp_path: Path) -> None:
    class Provider:
        async def stream_request(self, messages, **options):
            yield TextDelta("partial", index=0)
            yield ErrorEvent("boom", "overloaded_error")

    path = tmp_path / "trace.debug.jsonl"
    agent = AgentService(Provider(), debug_recorder=DebugRecorder(path))  # type: ignore[arg-type]

    async def collect_events():
        return [event async for event in agent.stream("hello")]

    with pytest.raises(ProviderError):
        asyncio.run(collect_events())

    records = _read_records(path)
    assert [record["type"] for record in records] == [
        "request",
        "response_event",
        "response_event",
        "error",
    ]
    assert records[2]["event"]["kind"] == "error"
    assert records[3]["error_type"] == "ProviderError"
    assert "boom" in records[3]["message"]
