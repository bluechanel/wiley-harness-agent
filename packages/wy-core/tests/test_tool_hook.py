"""tool_hook 模块:工具审批钩子——批准、拒绝、异常与审计留痕。"""

import json

import pytest

from wy_core import (
    Agent,
    AuditLog,
    RealtimeAgent,
    ToolCall,
    ToolResult,
    ToolUseBlock,
    TurnEnd,
)
from wy_core.tool import ToolApproval, ToolHook

from core_realtime_helpers import FakeRealtimeModel, make_realtime_agent, run_realtime
from helpers import (
    AllowAllHook,
    BoomHook,
    BoomTool,
    DenyAllHook,
    DenyByNameHook,
    EchoTool,
    FakeModel,
    end_event,
    make_text_end,
    run_events,
)


# ── Agent 测试 ──────────────────────────────────────────────


def test_审批批准工具正常执行():
    """审批通过时工具运行,结果正常。"""
    model = FakeModel(
        [
            [
                end_event(
                    ToolUseBlock(id="t1", name="echo", input={"text": "abc"}),
                    stop_reason="tool_use",
                )
            ],
            [make_text_end("完成")],
        ]
    )
    agent = Agent(model=model, tools=[EchoTool()], tool_hook=AllowAllHook(), audit=None)
    events = run_events(agent, "调用一下")

    assert [type(e) for e in events] == [ToolCall, ToolResult, TurnEnd]
    assert events[1].content == "abc" and not events[1].is_error


def test_审批拒绝工具返回错误():
    """审批拒绝时工具不执行,返回 is_error=True。"""
    model = FakeModel(
        [
            [end_event(ToolUseBlock(id="t1", name="echo", input={}), stop_reason="tool_use")],
            [make_text_end("好")],
        ]
    )
    agent = Agent(model=model, tools=[EchoTool()], tool_hook=DenyAllHook(), audit=None)
    events = run_events(agent, "hi")

    result = events[1]
    assert isinstance(result, ToolResult) and result.is_error
    assert "测试拒绝" in result.content


def test_审批拒绝特定工具名():
    """只拒绝匹配名称的工具,其他工具照常执行。"""
    model = FakeModel(
        [
            [
                end_event(
                    ToolUseBlock(id="t1", name="boom", input={}),
                    ToolUseBlock(id="t2", name="echo", input={"text": "ok"}),
                    stop_reason="tool_use",
                )
            ],
            [make_text_end("完毕")],
        ]
    )
    agent = Agent(
        model=model,
        tools=[BoomTool(), EchoTool()],
        tool_hook=DenyByNameHook({"boom"}),
        audit=None,
    )
    events = run_events(agent, "试试")

    assert [type(e) for e in events] == [ToolCall, ToolCall, ToolResult, ToolResult, TurnEnd]
    # boom 被拒,echo 通过
    assert events[2].is_error and "禁止调用 boom" in events[2].content
    assert events[3].content == "ok" and not events[3].is_error


def test_审批钩子抛异常视为否决():
    """审批钩子抛异常时转为拒绝,回合不中断。"""
    model = FakeModel(
        [
            [end_event(ToolUseBlock(id="t1", name="echo", input={}), stop_reason="tool_use")],
            [make_text_end("继续")],
        ]
    )
    agent = Agent(model=model, tools=[EchoTool()], tool_hook=BoomHook(), audit=None)
    events = run_events(agent, "go")

    result = events[1]
    assert isinstance(result, ToolResult) and result.is_error
    assert "审批服务挂了" in result.content
    assert isinstance(events[-1], TurnEnd)


def test_审批否决产生审计记录(tmp_path):
    """否决时审计日志包含 tool_approval 记录。"""
    audit_path = tmp_path / "audit.jsonl"
    model = FakeModel(
        [
            [end_event(ToolUseBlock(id="t1", name="echo", input={}), stop_reason="tool_use")],
            [make_text_end("好")],
        ]
    )
    agent = Agent(
        model=model,
        tools=[EchoTool()],
        tool_hook=DenyAllHook(),
        audit=AuditLog(audit_path),
    )
    run_events(agent, "hi")

    kinds = [
        json.loads(line)["kind"]
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert "tool_approval" in kinds
    # 找到 tool_approval 那条,验证内容
    approval_line = next(
        line for line in audit_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["kind"] == "tool_approval"
    )
    approval = json.loads(approval_line)
    assert approval["allowed"] is False
    assert approval["name"] == "echo"


def test_审批通过产生审计记录(tmp_path):
    """批准时审计日志也包含 tool_approval 记录。"""
    audit_path = tmp_path / "audit.jsonl"
    model = FakeModel(
        [
            [
                end_event(
                    ToolUseBlock(id="t1", name="echo", input={"text": "x"}),
                    stop_reason="tool_use",
                )
            ],
            [make_text_end("好")],
        ]
    )
    agent = Agent(
        model=model,
        tools=[EchoTool()],
        tool_hook=AllowAllHook(),
        audit=AuditLog(audit_path),
    )
    run_events(agent, "hi")

    kinds = [
        json.loads(line)["kind"]
        for line in audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert "tool_approval" in kinds


def test_无审批钩子时行为不变():
    """不传 tool_hook 时与原有行为完全一致(向后兼容)。"""
    model = FakeModel(
        [
            [
                end_event(
                    ToolUseBlock(id="t1", name="echo", input={"text": "abc"}),
                    stop_reason="tool_use",
                )
            ],
            [make_text_end("完成")],
        ]
    )
    agent = Agent(model=model, tools=[EchoTool()], audit=None)  # 不传 tool_hook
    events = run_events(agent, "调用一下")

    assert [type(e) for e in events] == [ToolCall, ToolResult, TurnEnd]
    assert events[1].content == "abc" and not events[1].is_error


# ── RealtimeAgent 测试 ──────────────────────────────────────


def test_realtime_审批批准工具正常执行():
    """RealtimeAgent 审批通过时工具正常执行并回写结果。"""
    from wy_core import AssistantTranscript, FunctionCall, ResponseDone, UserTranscript

    agent, model = make_realtime_agent(
        [
            UserTranscript(text="调用工具"),
            FunctionCall(call_id="c1", name="echo", arguments={"text": "abc"}),
            ResponseDone(),
            AssistantTranscript(text="完成"),
        ],
        tools=(EchoTool(),),
        audit=None,
    )
    agent._tool_hook = AllowAllHook()

    events = run_realtime(agent)
    tool_results = [e for e in events if isinstance(e, ToolResult)]
    assert len(tool_results) == 1
    assert tool_results[0].content == "abc" and not tool_results[0].is_error

    # 确认结果已回写给模型
    tool_results_sent = model.sent_of_type("tool_result")
    assert len(tool_results_sent) == 1
    assert tool_results_sent[0] == ("c1", "abc")


def test_realtime_审批拒绝工具返回错误():
    """RealtimeAgent 审批拒绝时不执行工具,返回 is_error=True。"""
    from wy_core import AssistantTranscript, FunctionCall, ResponseDone, UserTranscript

    agent, model = make_realtime_agent(
        [
            UserTranscript(text="调用工具"),
            FunctionCall(call_id="c1", name="echo", arguments={"text": "abc"}),
            ResponseDone(),
            AssistantTranscript(text="知道了"),
        ],
        tools=(EchoTool(),),
        audit=None,
    )
    agent._tool_hook = DenyAllHook()

    events = run_realtime(agent)
    tool_results = [e for e in events if isinstance(e, ToolResult)]
    assert len(tool_results) == 1
    assert tool_results[0].is_error
    assert "测试拒绝" in tool_results[0].content

    # 拒绝后仍然回写错误结果给模型
    tool_results_sent = model.sent_of_type("tool_result")
    assert len(tool_results_sent) == 1
    assert "Error:" in tool_results_sent[0][1] or "被拒绝" in tool_results_sent[0][1]


def test_realtime_审批钩子异常视为否决():
    """RealtimeAgent 审批钩子抛异常时转为拒绝。"""
    from wy_core import AssistantTranscript, FunctionCall, ResponseDone, UserTranscript

    agent, model = make_realtime_agent(
        [
            UserTranscript(text="调用工具"),
            FunctionCall(call_id="c1", name="echo", arguments={"text": "abc"}),
            ResponseDone(),
            AssistantTranscript(text="收到"),
        ],
        tools=(EchoTool(),),
        audit=None,
    )
    agent._tool_hook = BoomHook()

    events = run_realtime(agent)
    tool_results = [e for e in events if isinstance(e, ToolResult)]
    assert len(tool_results) == 1
    assert tool_results[0].is_error
    assert "审批服务挂了" in tool_results[0].content
