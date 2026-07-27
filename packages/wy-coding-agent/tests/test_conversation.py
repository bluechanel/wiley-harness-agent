"""ConversationService:wy-core Agent 流与持久记录的编排。"""

import asyncio
from pathlib import Path

import pytest

from wy_core import (
    Agent,
    ModelError,
    Session,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolUseBlock,
    Usage,
)

from wy_coding_agent.conversation import ConversationService
from wy_coding_agent.session import SessionStore

from app_helpers import BoomTool, FakeModel, drain, end_event, make_text_end


def _service(tmp_path: Path, model, *, tools=(), session=None):
    store = SessionStore(sessions_dir=tmp_path)
    agent = Agent(model=model, tools=tools, session=session, audit=None)
    return ConversationService(agent, store), store


def test_stream_persists_tool_records_in_order(tmp_path: Path) -> None:
    model = FakeModel(
        [
            [
                ThinkingDelta("think"),
                end_event(
                    ThinkingBlock(thinking="think"),
                    ToolUseBlock(id="call-1", name="boom", input={"command": "ls"}),
                    stop_reason="tool_use",
                ),
            ],
            [TextDelta("done"), make_text_end("done")],
        ]
    )
    service, store = _service(tmp_path, model, tools=[BoomTool()])

    drain(service)

    assert [(record.role, record.kind) for record in store.records] == [
        ("user", "input"),
        ("tool_call", "tool_call"),
        ("tool_output", "tool_output"),
        ("assistant", "thinking"),
        ("assistant", "answer"),
    ]
    tool_call = store.records[1]
    assert tool_call.content == {"command": "ls"}
    assert tool_call.metadata == {"tool_name": "boom", "tool_call_id": "call-1"}
    tool_output = store.records[2]
    assert tool_output.content == "Error: 炸了"
    assert tool_output.metadata == {
        "tool_name": "boom",
        "tool_call_id": "call-1",
        "is_error": True,
    }
    assert store.records[3].content == "think"
    assert store.records[4].content == "done"


def test_stream_persists_context_tokens_and_restores(tmp_path: Path) -> None:
    model = FakeModel(
        [[TextDelta("done"), make_text_end("done", usage=Usage(input_tokens=7, output_tokens=3))]]
    )
    service, store = _service(tmp_path, model)

    drain(service)

    answer = store.records[-1]
    assert (answer.metadata or {}).get("context_tokens") == 10
    assert answer.usage == Usage(input_tokens=7, output_tokens=3)
    assert service.total_usage == Usage(input_tokens=7, output_tokens=3)
    assert service.last_context_tokens == 10

    restored = SessionStore(store.session_id, sessions_dir=tmp_path)
    assert restored.total_usage == Usage(input_tokens=7, output_tokens=3)
    assert restored.last_context_tokens == 10


def test_stream_records_per_turn_usage_delta(tmp_path: Path) -> None:
    model = FakeModel(
        [
            [make_text_end("一", usage=Usage(input_tokens=10, output_tokens=5))],
            [make_text_end("二", usage=Usage(input_tokens=30, output_tokens=5))],
        ]
    )
    service, store = _service(tmp_path, model)

    drain(service, "第一")
    drain(service, "第二")

    answers = [r for r in store.records if r.kind == "answer"]
    assert answers[1].usage == Usage(input_tokens=30, output_tokens=5)  # 本回合增量
    assert answers[1].total_usage == Usage(input_tokens=40, output_tokens=10)  # 累计


def test_stream_error_writes_error_record_and_reraises(tmp_path: Path) -> None:
    model = FakeModel([[ModelError("网络中断")]])
    service, store = _service(tmp_path, model)

    with pytest.raises(ModelError):
        drain(service)

    last = store.records[-1]
    assert (last.role, last.kind) == ("assistant", "error")
    assert last.content == "网络中断"


def test_stream_records_compaction(tmp_path: Path) -> None:
    session = Session(max_context_tokens=1, keep_recent=1)
    model = FakeModel(
        [
            [make_text_end("第一答")],
            [make_text_end("这是摘要")],  # 第二回合开头的压缩请求
            [make_text_end("第二答")],
        ]
    )
    service, store = _service(tmp_path, model, session=session)

    drain(service, "第一问")
    drain(service, "第二问")

    compactions = [r for r in store.records if r.kind == "compaction"]
    assert len(compactions) == 1
    assert compactions[0].role == "assistant"
    assert compactions[0].content == "这是摘要"
    assert compactions[0].metadata == {"dropped": 2}
