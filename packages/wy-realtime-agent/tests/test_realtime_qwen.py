"""QwenRealtimeModel 翻译层测试与全链路冒烟。

编排语义(打断、收集式 function calling、send_user_text 排队等)的测试
随实现移居 wy-core(tests/test_core_realtime_agent.py);本文件覆盖本包
职责:wire 事件 → 类型化事件的翻译、发送侧 wire 载荷形状,以及经
``wy_core.RealtimeAgent`` 的端到端冒烟回归。
"""

import asyncio
import base64
import json

from wy_core import (
    AssistantTranscript,
    AssistantTranscriptDelta,
    AudioDelta,
    ErrorEvent,
    FunctionCall,
    Interrupted,
    ResponseDone,
    ResponseStarted,
    SessionEnded,
    SessionReady,
    SpeechStarted,
    SpeechStopped,
    Tool,
    ToolCall,
    ToolResult,
    ToolResultsSubmitted,
    TurnCommitted,
    TurnDiscarded,
    UserTranscript,
    UserTranscriptDelta,
)

from wy_realtime_agent.protocol import RealtimeClient
from wy_realtime_agent.qwen import QwenRealtimeModel

from realtime_helpers import (
    FakeSpeaker,
    FakeWebSocket,
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


def make_model(script=()) -> tuple[QwenRealtimeModel, FakeWebSocket]:
    config = make_config()
    ws = FakeWebSocket(script)
    client = RealtimeClient(config.url, config.api_key, config.model, connect=ws.connector())
    return QwenRealtimeModel(config, client=client), ws


def collect_events(model: QwenRealtimeModel) -> list:
    async def go() -> list:
        await model.connect()
        return [event async for event in model.events()]

    return asyncio.run(go())


def test_events_translate_wire_dicts_to_typed_events() -> None:
    pcm = b"\x01\x02\x03\x04"
    model, _ws = make_model(
        [
            {"type": "session.created"},  # 词汇之外:静默忽略
            {"type": "session.updated", "session": {"voice": "x"}},
            {"type": "response.created", "response": {"id": "resp_1"}},
            {"type": "response.audio.delta", "delta": base64.b64encode(pcm).decode()},
            {"type": "input_audio_buffer.speech_started"},
            {"type": "input_audio_buffer.speech_stopped", "audio_end_ms": 3400},
            {"type": "input_audio_buffer.speech_stopped", "reason": "turn_invalid"},
            {"type": "input_audio_buffer.committed", "item_id": "item_0"},
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "item_id": "item_0",
                "text": "你",
                "stash": "好",
            },
            {"type": "conversation.item.input_audio_transcription.delta", "text": "好"},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "你好",
            },
            {
                "type": "conversation.item.input_audio_transcription.failed",
                "error": {"type": "transcription_error", "message": "ASR failed"},
            },
            {
                "type": "conversation.item.ambient_audio_transcription.completed",
                "item_id": "item_1",
                "text": "嗯",
            },
            {"type": "response.audio_transcript.delta", "delta": "你好"},
            {"type": "response.audio_transcript.done", "transcript": "你好呀"},
            {"type": "response.text.delta", "delta": "文本增量"},
            {"type": "response.text.done", "text": "文本模态全文"},
            {
                "type": "response.function_call_arguments.done",
                "call_id": "c1",
                "name": "echo",
                "arguments": json.dumps({"text": "hi"}, ensure_ascii=False),
            },
            {"type": "response.done", "response": {"status": "completed"}},
            {"type": "response.done", "response": {"status": "cancelled"}},
            {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}},
            {"type": "voiceprint.enroll.completed"},  # 词汇之外:静默忽略
        ]
    )

    assert collect_events(model) == [
        SessionReady(),
        ResponseStarted(response_id="resp_1"),
        AudioDelta(pcm=pcm),
        SpeechStarted(),
        SpeechStopped(reason=None),
        SpeechStopped(reason="turn_invalid"),
        TurnCommitted(),
        UserTranscriptDelta(text="你", stash="好"),
        UserTranscriptDelta(text="好", stash=""),
        UserTranscript(text="你好"),
        ErrorEvent(type="transcription_error", message="ASR failed"),
        TurnDiscarded(),
        AssistantTranscriptDelta(text="你好"),
        AssistantTranscript(text="你好呀"),
        AssistantTranscriptDelta(text="文本增量"),
        AssistantTranscript(text="文本模态全文"),
        FunctionCall(call_id="c1", name="echo", arguments={"text": "hi"}),
        ResponseDone(cancelled=False),
        ResponseDone(cancelled=True),
        ErrorEvent(type="invalid_request_error", message="bad"),
    ]


def test_function_call_arguments_fall_back_to_empty_dict() -> None:
    def call_done(arguments: object) -> dict:
        return {
            "type": "response.function_call_arguments.done",
            "call_id": "c1",
            "name": "echo",
            "arguments": arguments,
        }

    model, _ws = make_model(
        [call_done("{残缺"), call_done(""), call_done('["非对象"]'), call_done(None)]
    )

    assert [event.arguments for event in collect_events(model)] == [{}, {}, {}, {}]


def test_send_methods_encode_qwen_wire_events() -> None:
    model, ws = make_model()

    async def go() -> dict:
        await model.connect()
        session = await model.update_session(system="做个助手", tools=(EchoTool(),))
        await model.send_audio(b"\x01\x02")
        await model.send_user_text("后台指令")
        await model.send_tool_result("c1", "结果")
        await model.create_response()
        await model.cancel_response()
        await model.close()
        return session

    session = asyncio.run(go())

    assert [event["type"] for event in ws.sent] == [
        "session.update",
        "input_audio_buffer.append",
        "conversation.item.create",
        "conversation.item.create",
        "response.create",
        "response.cancel",
    ]
    # update_session 返回的载荷即 wire 上实际发送的 session(供审计)。
    assert ws.sent[0]["session"] == session
    assert session["instructions"] == "做个助手"
    assert session["tools"][0]["function"]["name"] == "echo"
    assert ws.sent[1]["audio"] == base64.b64encode(b"\x01\x02").decode("ascii")
    assert ws.sent[2]["item"] == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "后台指令"}],
    }
    assert ws.sent[3]["item"] == {
        "type": "function_call_output",
        "call_id": "c1",
        "output": "结果",
    }
    assert ws.closed


def test_full_stack_tool_round_through_core_agent() -> None:
    agent, ws = make_agent(
        [
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "帮我 echo",
            },
            {"type": "response.created", "response": {"id": "resp_1"}},
            {
                "type": "response.function_call_arguments.done",
                "call_id": "c1",
                "name": "echo",
                "arguments": json.dumps({"text": "hi"}, ensure_ascii=False),
            },
            {"type": "response.done", "response": {"id": "resp_1", "status": "completed"}},
            {"type": "response.audio_transcript.done", "transcript": "结果是 hi"},
        ],
        tools=(EchoTool(),),
    )

    events = run_agent(agent)

    assert events == [
        UserTranscript(text="帮我 echo"),
        ResponseStarted(response_id="resp_1"),
        ResponseDone(cancelled=False),
        ToolCall(id="c1", name="echo", input={"text": "hi"}),
        ToolResult(id="c1", name="echo", content="hi", is_error=False),
        ToolResultsSubmitted(count=1),
        AssistantTranscript(text="结果是 hi"),
        SessionEnded(reason="服务端关闭了连接"),
    ]
    update = ws.sent[0]
    assert update["type"] == "session.update"
    assert update["session"]["turn_detection"]["type"] == "server_vad"
    assert update["session"]["tools"][0]["function"]["name"] == "echo"
    outputs = ws.sent_of_type("conversation.item.create")
    assert [event["item"] for event in outputs] == [
        {"type": "function_call_output", "call_id": "c1", "output": "hi"}
    ]
    assert len(ws.sent_of_type("response.create")) == 1
    assert ws.closed


def test_full_stack_interruption_suppresses_residual_audio() -> None:
    speaker = FakeSpeaker()
    first, late = b"\x01\x01", b"\x02\x02"
    agent, ws = make_agent(
        [
            {"type": "response.created", "response": {"id": "resp_1"}},
            {"type": "response.audio.delta", "delta": base64.b64encode(first).decode()},
            {"type": "input_audio_buffer.speech_started"},
            # 打断后的残余音频必须被抑制,不再进扬声器。
            {"type": "response.audio.delta", "delta": base64.b64encode(late).decode()},
            {"type": "response.done", "response": {"id": "resp_1", "status": "cancelled"}},
        ],
        speaker=speaker,
    )

    events = run_agent(agent)

    assert Interrupted(response_id="resp_1") in events
    assert speaker.played == [first]
    assert len(ws.sent_of_type("response.cancel")) == 1
