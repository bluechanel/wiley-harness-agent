"""Tests for the realtime protocol layer: session config assembly and the WS client."""

import asyncio
import base64

import pytest

from wy_core import Tool

from wy_realtime_agent.protocol import RealtimeClient, RealtimeError, build_session_config

from realtime_helpers import FakeWebSocket, make_config


class EchoTool(Tool):
    name = "echo"
    description = "原样返回 text 参数"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    def execute(self, input: dict) -> str:
        return str(input.get("text", ""))


def test_build_session_config_server_vad() -> None:
    config = make_config(vad_threshold=0.3, vad_silence_ms=600, max_history_turns=30)

    session = build_session_config(config, [])

    assert session["modalities"] == ["text", "audio"]
    assert session["voice"] == "longanqian"
    assert session["input_audio_format"] == "pcm"
    assert session["output_audio_format"] == "pcm"
    assert session["max_history_turns"] == 30
    assert session["turn_detection"] == {
        "type": "server_vad",
        "threshold": 0.3,
        "silence_duration_ms": 600,
    }
    assert "instructions" not in session  # 空 instructions 不发
    assert "tools" not in session  # 无工具不发


def test_build_session_config_smart_turn_and_instructions() -> None:
    config = make_config(mode="smart_turn", instructions="你是语音助手")

    session = build_session_config(config, [])

    assert session["turn_detection"] == {"type": "smart_turn"}
    assert session["instructions"] == "你是语音助手"


def test_build_session_config_converts_tools_to_function_schema() -> None:
    session = build_session_config(make_config(), [EchoTool()])

    assert session["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "原样返回 text 参数",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
            },
        }
    ]


def _connected_client(ws: FakeWebSocket) -> RealtimeClient:
    client = RealtimeClient(
        "wss://example.test/realtime", "sk-test", "test-model", connect=ws.connector()
    )
    asyncio.run(client.connect())
    return client


def test_client_connect_builds_url_and_auth_headers() -> None:
    ws = FakeWebSocket()

    _connected_client(ws)

    assert ws.connect_url == "wss://example.test/realtime?model=test-model"
    assert ws.connect_headers is not None
    assert ws.connect_headers["Authorization"] == "Bearer sk-test"


def test_client_send_events_add_incrementing_event_ids() -> None:
    ws = FakeWebSocket()
    client = _connected_client(ws)

    async def go() -> None:
        await client.update_session({"voice": "longanqian"})
        await client.append_audio(b"\x01\x02")
        await client.cancel_response()
        await client.create_response()
        await client.create_item({"type": "function_call_output", "call_id": "c1", "output": "ok"})

    asyncio.run(go())

    types = [event["type"] for event in ws.sent]
    assert types == [
        "session.update",
        "input_audio_buffer.append",
        "response.cancel",
        "response.create",
        "conversation.item.create",
    ]
    assert [event["event_id"] for event in ws.sent] == [
        f"event_{index}" for index in range(1, 6)
    ]
    assert ws.sent[0]["session"] == {"voice": "longanqian"}
    assert ws.sent[1]["audio"] == base64.b64encode(b"\x01\x02").decode("ascii")
    assert ws.sent[4]["item"]["call_id"] == "c1"


def test_client_events_decodes_server_stream_and_close() -> None:
    ws = FakeWebSocket([{"type": "session.created"}, {"type": "response.done"}])
    client = _connected_client(ws)

    async def go() -> list[dict]:
        events = [event async for event in client.events()]
        await client.close()
        return events

    events = asyncio.run(go())

    assert [event["type"] for event in events] == ["session.created", "response.done"]
    assert ws.closed


def test_client_requires_connect_before_use() -> None:
    client = RealtimeClient("wss://example.test", "k", "m", connect=FakeWebSocket().connector())

    with pytest.raises(RealtimeError):
        asyncio.run(client.send_event({"type": "response.create"}))


def test_client_connect_failure_raises_realtime_error() -> None:
    async def broken_connect(url: str, headers: dict) -> None:
        raise OSError("no route to host")

    client = RealtimeClient("wss://example.test", "k", "m", connect=broken_connect)

    with pytest.raises(RealtimeError, match="no route to host"):
        asyncio.run(client.connect())
