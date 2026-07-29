"""Tests for the RealtimeAgent orchestration: transcripts, function calling,
interruption, echo suppression and auditing."""

import base64
import json
from pathlib import Path

import pytest

from wy_core import Tool, ToolCall, ToolResult

from wy_realtime_agent.agent import (
    AssistantTranscript,
    Interrupted,
    RealtimeAgent,
    SessionEnded,
    UserTranscript,
)
from wy_realtime_agent.protocol import RealtimeClient

from realtime_helpers import (
    FakeMic,
    FakeSpeaker,
    FakeWebSocket,
    WaitFor,
    make_agent,
    make_config,
    run_agent,
)


class EchoTool(Tool):
    name = "echo"
    description = "原样返回 text 参数"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    def execute(self, input: dict) -> str:
        return str(input.get("text", ""))


class BoomTool(Tool):
    name = "boom"
    description = "总是抛错"
    parameters = {"type": "object", "properties": {}}

    def execute(self, input: dict) -> str:
        raise RuntimeError("炸了")


def _call_done(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "type": "response.function_call_arguments.done",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments, ensure_ascii=False),
    }


def test_run_sends_session_update_then_surfaces_transcripts() -> None:
    agent, ws = make_agent(
        [
            {"type": "session.created"},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "你好",
            },
            {"type": "response.audio_transcript.done", "transcript": "你好呀"},
        ],
        tools=(EchoTool(),),
    )

    events = run_agent(agent)

    assert events == [
        UserTranscript(text="你好"),
        AssistantTranscript(text="你好呀"),
        SessionEnded(reason="服务端关闭了连接"),
    ]
    update = ws.sent[0]
    assert update["type"] == "session.update"
    assert update["session"]["turn_detection"]["type"] == "server_vad"
    assert update["session"]["tools"][0]["function"]["name"] == "echo"
    assert ws.closed


def test_run_stops_audio_and_marks_lifecycle() -> None:
    mic, speaker = FakeMic(), FakeSpeaker()
    agent, _ws = make_agent([], mic=mic, speaker=speaker)

    run_agent(agent)

    assert mic.started and mic.stopped
    assert speaker.started and speaker.stopped


def test_function_calls_collected_and_executed_after_response_done() -> None:
    agent, ws = make_agent(
        [
            {"type": "response.created", "response": {"id": "resp_1"}},
            _call_done("c1", "echo", {"text": "hi"}),
            _call_done("c2", "nope", {}),
            {"type": "response.done", "response": {"id": "resp_1", "status": "completed"}},
        ],
        tools=(EchoTool(),),
    )

    events = run_agent(agent)

    assert events[:-1] == [
        ToolCall(id="c1", name="echo", input={"text": "hi"}),
        ToolResult(id="c1", name="echo", content="hi", is_error=False),
        ToolCall(id="c2", name="nope", input={}),
        ToolResult(id="c2", name="nope", content="Error: unknown tool nope", is_error=True),
    ]

    outputs = ws.sent_of_type("conversation.item.create")
    assert [event["item"] for event in outputs] == [
        {"type": "function_call_output", "call_id": "c1", "output": "hi"},
        {"type": "function_call_output", "call_id": "c2", "output": "Error: unknown tool nope"},
    ]
    # 全部结果回写完成后,恰好触发一次二轮推理,且在输出之后。
    creates = ws.sent_of_type("response.create")
    assert len(creates) == 1
    assert ws.sent.index(creates[0]) > ws.sent.index(outputs[-1])


def test_tool_exception_becomes_error_output_without_breaking_session() -> None:
    agent, ws = make_agent(
        [
            {"type": "response.created", "response": {"id": "resp_1"}},
            _call_done("c1", "boom", {}),
            {"type": "response.done", "response": {"status": "completed"}},
        ],
        tools=(BoomTool(),),
    )

    events = run_agent(agent)

    assert ToolResult(id="c1", name="boom", content="Error: 炸了", is_error=True) in events
    assert events[-1] == SessionEnded(reason="服务端关闭了连接")
    assert ws.sent_of_type("conversation.item.create")[0]["item"]["output"] == "Error: 炸了"


def test_cancelled_response_drops_pending_calls() -> None:
    agent, ws = make_agent(
        [
            {"type": "response.created", "response": {"id": "resp_1"}},
            _call_done("c1", "echo", {"text": "hi"}),
            {"type": "response.done", "response": {"status": "cancelled"}},
        ],
        tools=(EchoTool(),),
    )

    events = run_agent(agent)

    assert not [event for event in events if isinstance(event, ToolCall)]
    assert ws.sent_of_type("conversation.item.create") == []
    assert ws.sent_of_type("response.create") == []


def test_speech_started_interrupts_playback_and_cancels_response() -> None:
    speaker = FakeSpeaker()
    first, late, fresh = b"\x01\x01", b"\x02\x02", b"\x03\x03"
    agent, ws = make_agent(
        [
            {"type": "response.created", "response": {"id": "resp_1"}},
            {"type": "response.audio.delta", "delta": base64.b64encode(first).decode()},
            {"type": "input_audio_buffer.speech_started"},
            # 打断后的残余音频必须被抑制,直到新 response 开始。
            {"type": "response.audio.delta", "delta": base64.b64encode(late).decode()},
            {"type": "response.done", "response": {"id": "resp_1", "status": "cancelled"}},
            {"type": "response.created", "response": {"id": "resp_2"}},
            {"type": "response.audio.delta", "delta": base64.b64encode(fresh).decode()},
        ],
        speaker=speaker,
    )

    events = run_agent(agent)

    assert Interrupted(response_id="resp_1") in events
    assert speaker.cleared == 1
    assert speaker.played == [first, fresh]
    assert len(ws.sent_of_type("response.cancel")) == 1


def test_speech_started_without_active_response_only_clears_playback() -> None:
    speaker = FakeSpeaker()
    agent, ws = make_agent([{"type": "input_audio_buffer.speech_started"}], speaker=speaker)

    events = run_agent(agent)

    assert not [event for event in events if isinstance(event, Interrupted)]
    assert speaker.cleared == 1
    assert ws.sent_of_type("response.cancel") == []


def test_send_audio_streams_mic_chunks() -> None:
    chunk = b"\x10\x20" * 1600
    mic = FakeMic([chunk])
    agent, ws = make_agent(
        [WaitFor(lambda ws: bool(ws.sent_of_type("input_audio_buffer.append")))],
        mic=mic,
    )

    run_agent(agent)

    appends = ws.sent_of_type("input_audio_buffer.append")
    assert appends and appends[0]["audio"] == base64.b64encode(chunk).decode("ascii")


def test_echo_suppression_mutes_mic_while_playing() -> None:
    mic = FakeMic([b"\x7f\x00" * 1600] * 10)
    speaker = FakeSpeaker()
    speaker.playing = True
    agent, ws = make_agent(
        [WaitFor(lambda _ws: mic.reads >= 5)],
        config=make_config(echo_suppression=True),
        mic=mic,
        speaker=speaker,
    )

    run_agent(agent)

    assert ws.sent_of_type("input_audio_buffer.append") == []


def test_noise_gate_passes_loud_speech_during_playback() -> None:
    quiet, loud = b"\x00\x00" * 1600, b"\x00\x7f" * 1600
    speaker = FakeSpeaker()
    speaker.playing = True
    agent, ws = make_agent(
        [WaitFor(lambda ws: bool(ws.sent_of_type("input_audio_buffer.append")))],
        config=make_config(echo_suppression=False),
        mic=FakeMic([quiet, loud]),
        speaker=speaker,
    )

    run_agent(agent)

    appends = ws.sent_of_type("input_audio_buffer.append")
    assert [event["audio"] for event in appends] == [base64.b64encode(loud).decode("ascii")]


def test_server_error_event_is_audited_not_fatal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    ws = FakeWebSocket(
        [
            {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}},
            {"type": "response.audio_transcript.done", "transcript": "还在"},
        ]
    )
    config = make_config()
    client = RealtimeClient(config.url, config.api_key, config.model, connect=ws.connector())
    agent = RealtimeAgent(
        client=client, config=config, mic=FakeMic(), speaker=FakeSpeaker()
    )  # audit 缺省开启 → 写 CWD/.wy_audit/

    events = run_agent(agent)

    assert AssistantTranscript(text="还在") in events
    audit_files = list((tmp_path / ".wy_audit").glob("*.jsonl"))
    assert len(audit_files) == 1
    kinds = [
        json.loads(line)["kind"]
        for line in audit_files[0].read_text(encoding="utf-8").splitlines()
    ]
    assert kinds[:2] == ["agent_start", "session_update"]
    assert "error" in kinds and "assistant_transcript" in kinds


def test_duplicate_tool_names_rejected() -> None:
    config = make_config()
    client = RealtimeClient(config.url, config.api_key, config.model)

    with pytest.raises(ValueError, match="工具名重复"):
        RealtimeAgent(
            client=client,
            config=config,
            tools=(EchoTool(), EchoTool()),
            mic=FakeMic(),
            speaker=FakeSpeaker(),
            audit=None,
        )
