"""plan 模式:reminder 注入通道、状态语义、exit_plan_mode 工具与持久化。"""

from pathlib import Path

import pytest

from wy_core import Agent, AgentState, Usage

from wy_coding_agent.conversation import ConversationService
from wy_coding_agent.reminders import (
    PLAN_MODE_EXITED_REMINDER,
    PLAN_MODE_REMINDER,
    PlanModeState,
)
from wy_coding_agent.session import SessionStore
from wy_coding_agent.tools.plan import ExitPlanModeTool

from app_helpers import FakeModel, drain, make_text_end


def _service(tmp_path: Path, model, state: PlanModeState):
    store = SessionStore(sessions_dir=tmp_path)
    agent = Agent(model=model, state=AgentState(extensions=(state,)), audit=None)
    service = ConversationService(agent, store, reminder_providers=(state,))
    return service, store


def test_plan_state_激活期每回合注入_退出后一次性提示() -> None:
    state = PlanModeState()
    assert state.provide() is None

    state.enable()
    assert state.provide() == PLAN_MODE_REMINDER
    assert state.provide() == PLAN_MODE_REMINDER  # 重复注入是刻意语义

    state.disable()
    assert state.provide() == PLAN_MODE_EXITED_REMINDER  # 仅一次
    assert state.provide() is None


def test_exit_tool_翻转状态并返回确认() -> None:
    state = PlanModeState()
    state.enable()
    tool = ExitPlanModeTool(state)

    result = tool.execute({"plan": "## 方案\n1. 做事"})
    assert not state.active
    assert "已退出" in result


def test_exit_tool_空计划拒绝_非激活态提示() -> None:
    state = PlanModeState()
    tool = ExitPlanModeTool(state)
    with pytest.raises(RuntimeError):
        tool.execute({"plan": "  "})
    assert "不在 plan 模式" in tool.execute({"plan": "x"})


def test_stream_注入_reminder_并落盘_metadata(tmp_path: Path) -> None:
    model = FakeModel([[make_text_end("好")]])
    state = PlanModeState()
    state.enable()
    service, store = _service(tmp_path, model, state)

    drain(service)

    # 模型收到的 user 消息:正文 + <system-reminder> 尾块
    first = model.calls[0]["messages"][0]
    assert [b.text for b in first.content] == [
        "hi",
        f"<system-reminder>\n{PLAN_MODE_REMINDER}\n</system-reminder>",
    ]
    # 落盘:user 记录 content 仍是原始输入,reminders 进 metadata
    user_record = store.records[0]
    assert user_record.content == "hi"
    assert user_record.metadata == {"reminders": [PLAN_MODE_REMINDER]}


def test_非_plan_模式不注入_metadata_为空(tmp_path: Path) -> None:
    model = FakeModel([[make_text_end("好")]])
    service, store = _service(tmp_path, model, PlanModeState())

    drain(service)

    first = model.calls[0]["messages"][0]
    assert [b.text for b in first.content] == ["hi"]
    assert store.records[0].metadata is None


def test_恢复会话重建含_reminder_的_user_消息(tmp_path: Path) -> None:
    store = SessionStore(sessions_dir=tmp_path)
    store.append_user("问", reminders=("处于 plan 模式",))
    store.append_assistant("答", kind="answer", usage=Usage(), total_usage=Usage())

    restored = SessionStore(store.session_id, sessions_dir=tmp_path)
    messages = restored.conversation_messages()
    assert [b.text for b in messages[0].content] == [
        "问",
        "<system-reminder>\n处于 plan 模式\n</system-reminder>",
    ]
    assert messages[1].text == "答"


def test_state_快照与恢复语义() -> None:
    state = PlanModeState()
    state.enable()
    assert state.snapshot() == {"active": True}

    fresh = PlanModeState()
    fresh.restore({"active": True})
    assert fresh.active and fresh.provide() == PLAN_MODE_REMINDER
    # 一次性退出提示不跨会话:disable 后恢复出的新实例不再补发
    fresh.restore({"active": False})
    assert fresh.provide() is None


def test_回合结束状态有变即落盘_无变不重复(tmp_path: Path) -> None:
    model = FakeModel([[make_text_end("一")], [make_text_end("二")]])
    state = PlanModeState()
    state.enable()
    service, store = _service(tmp_path, model, state)

    drain(service, "第一问")
    states = [r for r in store.records if r.role == "state"]
    assert [r.content for r in states] == [{"plan_mode": {"active": True}}]

    drain(service, "第二问")  # 状态未变,不追加新记录
    states = [r for r in store.records if r.role == "state"]
    assert len(states) == 1


def test_save_state_回合外即时落盘(tmp_path: Path) -> None:
    service, store = _service(tmp_path, FakeModel([]), PlanModeState())
    service.plan_mode.enable()
    service.save_state()
    assert store.latest_state() == {"plan_mode": {"active": True}}
    service.save_state()  # 幂等:无变化不重复
    assert len([r for r in store.records if r.role == "state"]) == 1


def test_恢复会话后_plan_模式仍在(tmp_path: Path) -> None:
    from wy_coding_agent import create_agent

    service = create_agent(
        model=FakeModel([[make_text_end("答")]]),
        tools=(),
        sessions_dir=tmp_path,
        audit=False,
    )
    service.plan_mode.enable()
    drain(service, "先聊一轮")
    session_id = service.session_id
    service.close()

    second = FakeModel([[make_text_end("续")]])
    restored = create_agent(
        session_id, model=second, tools=(), sessions_dir=tmp_path, audit=False
    )
    assert restored.plan_mode is not None and restored.plan_mode.active
    drain(restored, "继续")
    last_user = second.calls[0]["messages"][-1]
    assert PLAN_MODE_REMINDER in [
        b.text.removeprefix("<system-reminder>\n").removesuffix("\n</system-reminder>")
        for b in last_user.content
    ]
    restored.close()
