"""plan 模式:harness 状态 → system prompt 逐提交组装、状态语义、exit 工具与持久化。"""

from pathlib import Path

import pytest

from wy_core import Agent, AgentState, TextBlock, ToolUseBlock, Usage

from wy_coding_agent.conversation import ConversationService
from wy_coding_agent.prompt_template import TextProvider, build_prompt
from wy_coding_agent.reminders import HarnessState
from wy_coding_agent.session import SessionStore
from wy_coding_agent.tools.plan import ExitPlanModeTool

from app_helpers import FakeModel, drain, end_event, make_text_end


def _service(tmp_path: Path, model, *, harness: HarnessState | None = None, tools=()):
    """组装一个带 harness 状态驱动的 system_builder 的最小服务。"""
    harness = harness or HarnessState()
    providers = (TextProvider("base"),)
    builder = lambda: build_prompt(providers, harness=harness)
    agent = Agent(
        model=model,
        tools=tools,
        system=build_prompt(providers),
        system_builder=builder,
        state=AgentState(extensions=(harness,)),
        audit=None,
    )
    store = SessionStore(sessions_dir=tmp_path)
    service = ConversationService(agent, store)
    return service, store, harness


def test_harness_state_enable_disable_plan() -> None:
    harness = HarnessState()
    assert not harness.plan_active
    harness.enable_plan()
    assert harness.plan_active
    assert harness.snapshot() == {"plan_active": True}
    harness.disable_plan()
    assert not harness.plan_active


def test_harness_state_restore_兼容旧active() -> None:
    fresh = HarnessState()
    fresh.restore({"active": True})
    assert fresh.plan_active
    fresh.restore({"plan_active": False})
    assert not fresh.plan_active


def test_exit_tool_翻转状态并返回确认() -> None:
    harness = HarnessState()
    harness.enable_plan()
    tool = ExitPlanModeTool(harness)

    result = tool.execute({"plan": "## 方案\n1. 做事"})
    assert not harness.plan_active
    assert "已退出" in result


def test_exit_tool_空计划拒绝_非激活态提示() -> None:
    harness = HarnessState()
    tool = ExitPlanModeTool(harness)
    with pytest.raises(RuntimeError):
        tool.execute({"plan": "  "})
    assert "不在 plan 模式" in tool.execute({"plan": "x"})


def test_plan_激活_system_含_plan段_user消息无reminder(tmp_path: Path) -> None:
    model = FakeModel([[make_text_end("答")], [make_text_end("续答")]])
    service, _, harness = _service(tmp_path, model)
    drain(service, "问")
    assert "# Plan mode" not in (model.calls[0]["system"] or "")

    service.set_plan_mode(True)
    assert harness.plan_active
    drain(service, "继续")

    # system 由 system_builder 按 harness 状态逐提交组装;user 消息无 plan reminder
    assert "# Plan mode" in (model.calls[1]["system"] or "")
    last = model.calls[1]["messages"][-1]
    assert [b.text for b in last.content] == ["继续"]


def test_exit_tool_同回合下次迭代_system_无plan段(tmp_path: Path) -> None:
    harness = HarnessState()
    harness.enable_plan()
    model = FakeModel(
        [
            [
                end_event(
                    TextBlock("调研"),
                    ToolUseBlock(
                        id="t1", name="exit_plan_mode", input={"plan": "## 方案"}
                    ),
                    stop_reason="tool_use",
                )
            ],
            [make_text_end("开始实施")],
        ]
    )
    service, _, _ = _service(
        tmp_path, model, harness=harness, tools=(ExitPlanModeTool(harness),)
    )

    drain(service, "提交")

    assert not harness.plan_active
    assert "# Plan mode" in (model.calls[0]["system"] or "")  # 调用工具前仍在 plan
    assert "# Plan mode" not in (model.calls[1]["system"] or "")  # 工具后同回合已无


def test_非plan_模式_无reminder_metadata为空(tmp_path: Path) -> None:
    model = FakeModel([[make_text_end("好")]])
    service, store, _ = _service(tmp_path, model)
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


def test_回合结束状态有变即落盘_无变不重复(tmp_path: Path) -> None:
    model = FakeModel([[make_text_end("一")], [make_text_end("二")]])
    service, store, _ = _service(tmp_path, model)
    service.set_plan_mode(True)
    drain(service, "第一问")
    states = [r for r in store.records if r.role == "state"]
    assert [r.content for r in states] == [{"plan_mode": {"plan_active": True}}]

    drain(service, "第二问")  # 状态未变,不追加新记录
    states = [r for r in store.records if r.role == "state"]
    assert len(states) == 1


def test_save_state_回合外即时落盘(tmp_path: Path) -> None:
    service, store, _ = _service(tmp_path, FakeModel([]), harness=HarnessState())
    service.set_plan_mode(True)
    assert store.latest_state() == {"plan_mode": {"plan_active": True}}
    service.set_plan_mode(False)
    assert store.latest_state() == {"plan_mode": {"plan_active": False}}


def test_恢复会话后_plan_模式仍在(tmp_path: Path) -> None:
    from wy_coding_agent import create_agent

    service = create_agent(
        model=FakeModel([[make_text_end("答")]]),
        tools=(),
        sessions_dir=tmp_path,
        audit=False,
    )
    service.set_plan_mode(True)
    drain(service, "先聊一轮")
    session_id = service.session_id
    service.close()

    second = FakeModel([[make_text_end("续")]])
    restored = create_agent(
        session_id, model=second, tools=(), sessions_dir=tmp_path, audit=False
    )
    assert restored.plan_mode is not None and restored.plan_mode.plan_active
    # 恢复后 harness 状态驱动:首回合提交的 system 已含 plan 段
    drain(restored, "继续")
    assert "# Plan mode" in (second.calls[0]["system"] or "")
    restored.close()
