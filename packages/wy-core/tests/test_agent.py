"""agent 模块:循环控制流、工具执行、压缩触发与审计留痕。"""

import json

import pytest

from wy_core import (
    Agent,
    AgentError,
    Compaction,
    ModelError,
    Session,
    TextBlock,
    TextDelta,
    ToolCall,
    ToolResult,
    ToolResultBlock,
    ToolUseBlock,
    TurnEnd,
    Usage,
)

from helpers import BoomTool, EchoTool, FakeModel, end_event, make_text_end, run_events


def test_纯文本回合():
    model = FakeModel([[TextDelta("你"), TextDelta("好"), make_text_end("你好")]])
    agent = Agent(model=model, audit=None)
    events = run_events(agent, "hi")

    assert [type(e) for e in events] == [TextDelta, TextDelta, TurnEnd]
    assert events[-1].context_tokens == 15
    assert [m.role for m in agent.session.messages] == ["user", "assistant"]
    assert model.calls[0]["tools"] is None  # 无工具时不带 tools


def test_工具回合():
    model = FakeModel(
        [
            [
                end_event(
                    TextBlock("我来调用工具"),
                    ToolUseBlock(id="t1", name="echo", input={"text": "abc"}),
                    stop_reason="tool_use",
                )
            ],
            [make_text_end("完成")],
        ]
    )
    agent = Agent(model=model, tools=[EchoTool()], audit=None)
    events = run_events(agent, "调用一下")

    assert [type(e) for e in events] == [ToolCall, ToolResult, TurnEnd]
    assert events[1].content == "abc" and not events[1].is_error
    # 第二次请求的末条消息是携带 tool_result 的 user 消息
    last = model.calls[1]["messages"][-1]
    assert last.role == "user"
    assert last.content == [ToolResultBlock(tool_use_id="t1", content="abc")]
    # 会话:user、assistant(tool_use)、user(tool_result)、assistant(答案)
    assert [m.role for m in agent.session.messages] == ["user", "assistant", "user", "assistant"]


def test_工具抛错不中断回合():
    model = FakeModel(
        [
            [end_event(ToolUseBlock(id="t1", name="boom", input={}), stop_reason="tool_use")],
            [make_text_end("已处理错误")],
        ]
    )
    agent = Agent(model=model, tools=[BoomTool()], audit=None)
    events = run_events(agent, "去吧")

    result = events[1]
    assert isinstance(result, ToolResult) and result.is_error
    assert result.content == "Error: 炸了"
    assert isinstance(events[-1], TurnEnd)  # 回合正常走完


def test_多工具并行执行():
    import threading

    from wy_core import Tool

    barrier = threading.Barrier(2)  # 两个工具必须同时在跑才能都通过

    class MeetTool(Tool):
        name = "meet"
        description = "在栅栏处会合"
        parameters = {"type": "object", "properties": {}}

        def execute(self, input: dict) -> str:
            barrier.wait(timeout=5)
            return "met"

    model = FakeModel(
        [
            [
                end_event(
                    ToolUseBlock(id="t1", name="meet", input={}),
                    ToolUseBlock(id="t2", name="meet", input={}),
                    stop_reason="tool_use",
                )
            ],
            [make_text_end("完")],
        ]
    )
    agent = Agent(model=model, tools=[MeetTool()], audit=None)
    events = run_events(agent, "并行")

    # 先产出全部 ToolCall,再按调用顺序产出 ToolResult
    assert [type(e) for e in events] == [ToolCall, ToolCall, ToolResult, ToolResult, TurnEnd]
    assert [e.id for e in events[:4]] == ["t1", "t2", "t1", "t2"]
    assert all(r.content == "met" and not r.is_error for r in events[2:4])


def test_未知工具名():
    model = FakeModel(
        [
            [end_event(ToolUseBlock(id="t1", name="nope", input={}), stop_reason="tool_use")],
            [make_text_end("好")],
        ]
    )
    agent = Agent(model=model, audit=None)
    events = run_events(agent, "试试")
    assert events[1].is_error and events[1].content == "Error: unknown tool nope"


def test_流未产出_model_end_报错():
    model = FakeModel([[TextDelta("半截")]])
    agent = Agent(model=model, audit=None)
    with pytest.raises(ModelError):
        run_events(agent, "hi")


def test_模型异常回滚本回合():
    model = FakeModel([[TextDelta("半"), ModelError("网络中断")]])
    agent = Agent(model=model, audit=None)
    with pytest.raises(ModelError):
        run_events(agent, "hi")
    assert agent.session.messages == []  # 本回合的 user 消息被回滚


def test_多回合失败只回滚当回合():
    model = FakeModel([[make_text_end("好")], [ModelError("挂了")]])
    agent = Agent(model=model, audit=None)
    run_events(agent, "第一")
    with pytest.raises(ModelError):
        run_events(agent, "第二")
    assert [m.text for m in agent.session.messages] == ["第一", "好"]


def test_超过最大迭代轮数():
    scripts = [
        [end_event(ToolUseBlock(id=f"t{i}", name="echo", input={}), stop_reason="tool_use")]
        for i in range(2)
    ]
    agent = Agent(model=FakeModel(scripts), tools=[EchoTool()], audit=None, max_iterations=2)
    with pytest.raises(AgentError):
        run_events(agent, "停不下来")


def test_工具重名拒绝():
    with pytest.raises(ValueError):
        Agent(model=FakeModel([]), tools=[EchoTool(), EchoTool()], audit=None)


def test_自动压缩触发():
    session = Session(max_context_tokens=1, keep_recent=1)
    model = FakeModel(
        [
            [make_text_end("第一答")],  # 第一回合
            [make_text_end("这是摘要")],  # 第二回合开头的压缩请求
            [make_text_end("第二答")],  # 第二回合正式请求
        ]
    )
    agent = Agent(model=model, session=session, audit=None)
    run_events(agent, "第一问")
    events = run_events(agent, "第二问")

    compaction = events[0]
    assert isinstance(compaction, Compaction)
    assert compaction.dropped == 2 and compaction.summary == "这是摘要"
    assert session.messages[0].text == "[早前对话摘要]\n这是摘要"
    # 压缩后的正式请求:摘要 + 保留的"第二问" + 本轮答案
    assert [m.text for m in session.messages[1:]] == ["第二问", "第二答"]


def test_审计默认开启且逐条留痕(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model = FakeModel(
        [
            [
                end_event(
                    ToolUseBlock(id="t1", name="echo", input={"text": "x"}),
                    stop_reason="tool_use",
                )
            ],
            [make_text_end("完")],
        ]
    )
    agent = Agent(model=model, tools=[EchoTool()])  # 省略 audit → 默认开启
    run_events(agent, "hi")

    files = list((tmp_path / ".wy_audit").glob("*.jsonl"))
    assert len(files) == 1
    kinds = [json.loads(line)["kind"] for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert kinds == [
        "agent_start",
        "request",
        "model_end",
        "tool_call",
        "tool_result",
        "request",
        "model_end",
    ]


def test_审计显式关闭不落盘(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = Agent(model=FakeModel([[make_text_end("好")]]), audit=None)
    run_events(agent, "hi")
    assert not (tmp_path / ".wy_audit").exists()


def test_模型收到的用量与累计(tmp_path):
    model = FakeModel(
        [
            [make_text_end("一", usage=Usage(input_tokens=10, output_tokens=5))],
            [make_text_end("二", usage=Usage(input_tokens=30, output_tokens=5))],
        ]
    )
    agent = Agent(model=model, audit=None)
    run_events(agent, "1")
    end = run_events(agent, "2")[-1]
    assert end.usage.input_tokens == 40  # 累计
    assert end.context_tokens == 35  # 最近一次
