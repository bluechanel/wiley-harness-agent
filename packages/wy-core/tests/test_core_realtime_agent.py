"""RealtimeAgent 编排测试:转写、function calling、打断、回声抑制、
后台文字指令注入与审计(以 FakeRealtimeModel 的类型化事件驱动)。"""

import asyncio
import json
from pathlib import Path

import pytest

from wy_core import (
    AssistantTranscript,
    AssistantTranscriptDelta,
    AudioDelta,
    AuditLog,
    ErrorEvent,
    FunctionCall,
    Interrupted,
    RealtimeAgent,
    RealtimeError,
    ResponseDone,
    ResponseStarted,
    SessionEnded,
    SessionReady,
    SpeechStarted,
    SpeechStopped,
    ToolCall,
    ToolResult,
    ToolResultsSubmitted,
    TurnCommitted,
    TurnDiscarded,
    UserTranscript,
    UserTranscriptDelta,
)

from core_realtime_helpers import (
    FakeMic,
    FakeRealtimeModel,
    FakeSpeaker,
    WaitFor,
    make_realtime_agent,
    run_realtime,
)
from helpers import BoomTool, EchoTool


def test_run_updates_session_then_surfaces_transcripts() -> None:
    agent, model = make_realtime_agent(
        [UserTranscript(text="你好"), AssistantTranscript(text="你好呀")],
        tools=(EchoTool(),),
        system="你是语音助手",
    )

    events = run_realtime(agent)

    assert events == [
        UserTranscript(text="你好"),
        AssistantTranscript(text="你好呀"),
        SessionEnded(reason="服务端关闭了连接"),
    ]
    kind, payload = model.sent[0]
    assert kind == "session.update"
    assert payload == {"instructions": "你是语音助手", "tools": ["echo"]}
    assert model.closed


def test_transcript_deltas_pass_through_in_order_without_audit(tmp_path: Path) -> None:
    agent, _model = make_realtime_agent(
        [
            UserTranscriptDelta(text="你", stash="好"),
            UserTranscriptDelta(text="好"),
            UserTranscript(text="你好"),
            ResponseStarted(response_id="resp_1"),
            AssistantTranscriptDelta(text="在"),
            AssistantTranscriptDelta(text="的"),
            AssistantTranscript(text="在的"),
            ResponseDone(),
        ],
        audit=AuditLog(tmp_path / "audit.jsonl"),
    )

    events = run_realtime(agent)

    assert events == [
        UserTranscriptDelta(text="你", stash="好"),
        UserTranscriptDelta(text="好"),
        UserTranscript(text="你好"),
        ResponseStarted(response_id="resp_1"),
        AssistantTranscriptDelta(text="在"),
        AssistantTranscriptDelta(text="的"),
        AssistantTranscript(text="在的"),
        ResponseDone(),
        SessionEnded(reason="服务端关闭了连接"),
    ]
    # 增量与生命周期信号仅供渲染/状态管理:审计只留完成级转写,不逐 delta 留痕。
    kinds = [
        json.loads(line)["kind"]
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert kinds == ["agent_start", "session_update", "user_transcript", "assistant_transcript"]


def test_lifecycle_events_surface_in_wire_order_for_state_management() -> None:
    agent, _model = make_realtime_agent(
        [
            SessionReady(),
            SpeechStarted(),
            UserTranscriptDelta(text="你好"),
            SpeechStopped(),
            TurnCommitted(),
            UserTranscript(text="你好"),
            ResponseStarted(response_id="resp_1"),
            AudioDelta(pcm=b"\x01\x02"),
            AssistantTranscriptDelta(text="你好呀"),
            AssistantTranscript(text="你好呀"),
            ResponseDone(),
        ]
    )

    events = run_realtime(agent)

    assert events == [
        SessionReady(),
        SpeechStarted(),
        UserTranscriptDelta(text="你好"),
        SpeechStopped(),
        TurnCommitted(),
        UserTranscript(text="你好"),
        ResponseStarted(response_id="resp_1"),
        AudioDelta(pcm=b"\x01\x02"),
        AssistantTranscriptDelta(text="你好呀"),
        AssistantTranscript(text="你好呀"),
        ResponseDone(),
        SessionEnded(reason="服务端关闭了连接"),
    ]


def test_interruption_suppresses_residual_transcript_deltas() -> None:
    agent, _model = make_realtime_agent(
        [
            ResponseStarted(response_id="resp_1"),
            AssistantTranscriptDelta(text="早上"),
            SpeechStarted(),  # 打断
            AssistantTranscriptDelta(text="好呀"),  # 被打断响应的残余字幕:抑制
            ResponseDone(cancelled=True),
            ResponseStarted(response_id="resp_2"),
            AssistantTranscriptDelta(text="新回复"),
        ]
    )

    events = run_realtime(agent)

    assert Interrupted(response_id="resp_1") in events
    deltas = [event.text for event in events if isinstance(event, AssistantTranscriptDelta)]
    assert deltas == ["早上", "新回复"]


def test_run_stops_audio_and_marks_lifecycle() -> None:
    mic, speaker = FakeMic(), FakeSpeaker()
    agent, model = make_realtime_agent([], mic=mic, speaker=speaker)

    run_realtime(agent)

    assert mic.started and mic.stopped
    assert speaker.started and speaker.stopped
    assert model.closed


def test_transport_error_yields_session_ended() -> None:
    agent, model = make_realtime_agent(
        [UserTranscript(text="你好"), RealtimeError("连接异常关闭")]
    )

    events = run_realtime(agent)

    assert events == [
        UserTranscript(text="你好"),
        SessionEnded(reason="连接异常关闭"),
    ]
    assert model.closed


def test_function_calls_collected_and_executed_after_response_done() -> None:
    agent, model = make_realtime_agent(
        [
            ResponseStarted(response_id="resp_1"),
            FunctionCall(call_id="c1", name="echo", arguments={"text": "hi"}),
            FunctionCall(call_id="c2", name="nope"),
            ResponseDone(),
        ],
        tools=(EchoTool(),),
    )

    events = run_realtime(agent)

    assert events == [
        ResponseStarted(response_id="resp_1"),
        ResponseDone(),
        ToolCall(id="c1", name="echo", input={"text": "hi"}),
        ToolResult(id="c1", name="echo", content="hi", is_error=False),
        ToolCall(id="c2", name="nope", input={}),
        ToolResult(id="c2", name="nope", content="Error: unknown tool nope", is_error=True),
        ToolResultsSubmitted(count=2),
        SessionEnded(reason="服务端关闭了连接"),
    ]
    assert model.sent_of_type("tool_result") == [
        ("c1", "hi"),
        ("c2", "Error: unknown tool nope"),
    ]
    # 全部结果回写完成后,恰好触发一次二轮推理,且在回写之后。
    creates = model.indexes_of("response.create")
    assert len(creates) == 1
    assert creates[0] > model.indexes_of("tool_result")[-1]


def test_tool_exception_becomes_error_output_without_breaking_session() -> None:
    agent, model = make_realtime_agent(
        [
            ResponseStarted(response_id="resp_1"),
            FunctionCall(call_id="c1", name="boom"),
            ResponseDone(),
        ],
        tools=(BoomTool(),),
    )

    events = run_realtime(agent)

    assert ToolResult(id="c1", name="boom", content="Error: 炸了", is_error=True) in events
    assert events[-1] == SessionEnded(reason="服务端关闭了连接")
    assert model.sent_of_type("tool_result") == [("c1", "Error: 炸了")]


def test_cancelled_response_drops_pending_calls() -> None:
    agent, model = make_realtime_agent(
        [
            ResponseStarted(response_id="resp_1"),
            FunctionCall(call_id="c1", name="echo", arguments={"text": "hi"}),
            ResponseDone(cancelled=True),
        ],
        tools=(EchoTool(),),
    )

    events = run_realtime(agent)

    assert not [event for event in events if isinstance(event, ToolCall)]
    assert model.sent_of_type("tool_result") == []
    assert model.sent_of_type("response.create") == []


def test_speech_started_interrupts_playback_and_cancels_response() -> None:
    speaker = FakeSpeaker()
    first, late, fresh = b"\x01\x01", b"\x02\x02", b"\x03\x03"
    agent, model = make_realtime_agent(
        [
            ResponseStarted(response_id="resp_1"),
            AudioDelta(pcm=first),
            SpeechStarted(),
            # 打断后的残余音频必须被抑制,直到新 response 开始。
            AudioDelta(pcm=late),
            ResponseDone(cancelled=True),
            ResponseStarted(response_id="resp_2"),
            AudioDelta(pcm=fresh),
        ],
        speaker=speaker,
    )

    events = run_realtime(agent)

    assert Interrupted(response_id="resp_1") in events
    assert speaker.cleared == 1
    assert speaker.played == [first, fresh]
    assert len(model.sent_of_type("response.cancel")) == 1


def test_speech_started_without_active_response_only_clears_playback() -> None:
    speaker = FakeSpeaker()
    agent, model = make_realtime_agent([SpeechStarted()], speaker=speaker)

    events = run_realtime(agent)

    assert not [event for event in events if isinstance(event, Interrupted)]
    assert speaker.cleared == 1
    assert model.sent_of_type("response.cancel") == []


def test_send_audio_streams_mic_chunks() -> None:
    chunk = b"\x10\x20" * 1600
    mic = FakeMic([chunk])
    agent, model = make_realtime_agent(
        [WaitFor(lambda m: bool(m.sent_of_type("audio")))], mic=mic
    )

    run_realtime(agent)

    assert model.sent_of_type("audio")[0] == chunk


def test_echo_suppression_mutes_mic_while_playing() -> None:
    mic = FakeMic([b"\x7f\x00" * 1600] * 10)
    speaker = FakeSpeaker()
    speaker.playing = True
    agent, model = make_realtime_agent(
        [WaitFor(lambda _m: mic.reads >= 5)],
        mic=mic,
        speaker=speaker,
        echo_suppression=True,
    )

    run_realtime(agent)

    assert model.sent_of_type("audio") == []


def test_noise_gate_passes_loud_speech_during_playback() -> None:
    quiet, loud = b"\x00\x00" * 1600, b"\x00\x7f" * 1600
    speaker = FakeSpeaker()
    speaker.playing = True
    agent, model = make_realtime_agent(
        [WaitFor(lambda m: bool(m.sent_of_type("audio")))],
        mic=FakeMic([quiet, loud]),
        speaker=speaker,
        echo_suppression=False,
    )

    run_realtime(agent)

    assert model.sent_of_type("audio") == [loud]


def test_server_error_event_is_audited_not_fatal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    model = FakeRealtimeModel(
        [
            ErrorEvent(type="invalid_request_error", message="bad"),
            AssistantTranscript(text="还在"),
        ]
    )
    agent = RealtimeAgent(
        model=model, mic=FakeMic(), speaker=FakeSpeaker()
    )  # audit 缺省开启 → 写 CWD/.wy_audit/

    events = run_realtime(agent)

    assert ErrorEvent(type="invalid_request_error", message="bad") in events  # 透传给下游
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
    with pytest.raises(ValueError, match="工具名重复"):
        RealtimeAgent(
            model=FakeRealtimeModel(),
            tools=(EchoTool(), EchoTool()),
            mic=FakeMic(),
            speaker=FakeSpeaker(),
            audit=None,
        )


def test_send_user_text_idle_sends_immediately_and_audits(tmp_path: Path) -> None:
    agent, model = make_realtime_agent(
        [UserTranscript(text="你好")], audit=AuditLog(tmp_path / "audit.jsonl")
    )
    texts_when_sent: list[int] = []

    async def inject(event) -> None:
        if isinstance(event, UserTranscript):
            await agent.send_user_text("文字指令")
            texts_when_sent.append(len(model.sent_of_type("user_text")))

    run_realtime(agent, on_event=inject)

    assert texts_when_sent == [1]  # 空闲(不在听、不在答)时立即发出
    assert model.sent_of_type("user_text") == ["文字指令"]
    # 注入后紧跟一次响应触发,让模型立即执行指令。
    creates = model.indexes_of("response.create")
    assert len(creates) == 1
    assert creates[0] > model.indexes_of("user_text")[0]
    kinds = [
        json.loads(line)["kind"]
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert "user_text" in kinds


def test_send_user_text_during_response_blocked_until_done() -> None:
    agent, model = make_realtime_agent(
        [
            ResponseStarted(response_id="resp_1"),
            UserTranscript(text="触发"),
            ResponseDone(),
        ]
    )
    texts_when_queued: list[int] = []

    async def inject(event) -> None:
        if isinstance(event, UserTranscript):
            await agent.send_user_text("第一条")
            await agent.send_user_text("第二条")
            texts_when_queued.append(len(model.sent_of_type("user_text")))

    run_realtime(agent, on_event=inject)

    assert texts_when_queued == [0]  # 回答进行中被阻塞,未发出
    assert model.sent_of_type("user_text") == ["第一条", "第二条"]
    # 补发的多条指令合并为一次响应触发,且在全部指令之后。
    creates = model.indexes_of("response.create")
    assert len(creates) == 1
    assert creates[0] > model.indexes_of("user_text")[-1]


def test_queued_user_text_survives_interruption_and_waits_for_next_idle() -> None:
    agent, model = make_realtime_agent(
        [
            ResponseStarted(response_id="resp_1"),
            UserTranscript(text="触发"),
            SpeechStarted(),
            ResponseDone(cancelled=True),
            # 打断后仍处于"在听":cancelled 的 ResponseDone 不能补发指令。
            UserTranscript(text="新轮次"),
            ResponseStarted(response_id="resp_2"),
            ResponseDone(),
        ]
    )
    texts_after_cancel: list[int] = []

    async def inject(event) -> None:
        if isinstance(event, UserTranscript) and event.text == "触发":
            await agent.send_user_text("别丢了")
        if isinstance(event, UserTranscript) and event.text == "新轮次":
            texts_after_cancel.append(len(model.sent_of_type("user_text")))

    events = run_realtime(agent, on_event=inject)

    assert Interrupted(response_id="resp_1") in events
    assert texts_after_cancel == [0]  # 在听期间(含 cancelled done 之后)仍被阻塞
    # 新语音轮次答完回到空闲,指令照常补发并触发执行,不随打断丢弃。
    assert model.sent_of_type("user_text") == ["别丢了"]
    assert len(model.indexes_of("response.create")) == 1


def test_queued_user_text_waits_for_tool_second_round() -> None:
    agent, model = make_realtime_agent(
        [
            ResponseStarted(response_id="resp_1"),
            UserTranscript(text="触发"),
            FunctionCall(call_id="c1", name="echo", arguments={"text": "hi"}),
            ResponseDone(),
            ResponseStarted(response_id="resp_2"),
            ResponseDone(),
        ],
        tools=(EchoTool(),),
    )

    async def inject(event) -> None:
        if isinstance(event, UserTranscript):
            await agent.send_user_text("等二轮")

    run_realtime(agent, on_event=inject)

    # 带工具的回合到二轮(resp_2)结束才算真正空闲:先回写工具结果并触发二轮,
    # 二轮答完再注入指令并再触发一次执行。
    tool_results = model.indexes_of("tool_result")
    user_texts = model.indexes_of("user_text")
    creates = model.indexes_of("response.create")
    assert len(tool_results) == 1 and len(user_texts) == 1
    assert len(creates) == 2
    assert tool_results[0] < creates[0] < user_texts[0] < creates[1]


def test_send_user_text_while_listening_blocked_until_turn_answered() -> None:
    agent, model = make_realtime_agent(
        [
            SpeechStarted(),
            UserTranscript(text="语音提问"),
            ResponseStarted(response_id="resp_1"),
            ResponseDone(),
        ]
    )
    texts_when_queued: list[int] = []

    async def inject(event) -> None:
        if isinstance(event, UserTranscript):
            await agent.send_user_text("后台指令")
            texts_when_queued.append(len(model.sent_of_type("user_text")))

    run_realtime(agent, on_event=inject)

    assert texts_when_queued == [0]  # "在听"(语音轮次尚未开答)时同样被阻塞
    assert model.sent_of_type("user_text") == ["后台指令"]
    assert len(model.indexes_of("response.create")) == 1


def test_turn_discarded_ends_listening_and_flushes() -> None:
    agent, model = make_realtime_agent(
        [
            ResponseStarted(response_id="resp_1"),
            UserTranscript(text="触发"),
            SpeechStarted(),
            ResponseDone(cancelled=True),
            # 服务端判定这段语音为非有效轮次:不会再有新回答,靠 TurnDiscarded
            # 结束"在听",否则指令会一直卡到下一次语音轮次。
            TurnDiscarded(),
        ]
    )

    async def inject(event) -> None:
        if isinstance(event, UserTranscript):
            await agent.send_user_text("别卡住")

    events = run_realtime(agent, on_event=inject)

    assert TurnDiscarded() in events  # 判非轮次信号同样透传给下游
    assert model.sent_of_type("user_text") == ["别卡住"]
    assert len(model.indexes_of("response.create")) == 1


def test_error_event_clears_response_pending_so_texts_flow() -> None:
    agent, model = make_realtime_agent(
        [
            UserTranscript(text="第一次注入"),
            # 我们触发的响应被服务端拒绝:ResponseStarted 永远不会来,
            # error 事件必须兜底清掉"响应待建",否则后续指令永久卡队列。
            ErrorEvent(type="invalid_request_error", message="rejected"),
            UserTranscript(text="第二次注入"),
        ]
    )

    async def inject(event) -> None:
        if isinstance(event, UserTranscript) and event.text == "第一次注入":
            await agent.send_user_text("指令一")
        if isinstance(event, UserTranscript) and event.text == "第二次注入":
            await agent.send_user_text("指令二")

    run_realtime(agent, on_event=inject)

    assert model.sent_of_type("user_text") == ["指令一", "指令二"]
    assert len(model.indexes_of("response.create")) == 2


def test_send_user_text_before_connect_raises() -> None:
    agent, _model = make_realtime_agent([])

    with pytest.raises(RealtimeError, match="尚未连接"):
        asyncio.run(agent.send_user_text("hi"))
