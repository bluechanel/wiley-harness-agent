"""ToolSet:直接加载/懒加载划分、激活语义与查重。"""

import pytest

from wy_core import Agent, TextBlock, Tool, ToolSet, ToolUseBlock

from helpers import EchoTool, FakeModel, end_event, make_text_end, run_events


class LazyTool(Tool):
    name = "lazy"
    description = "懒加载工具"
    parameters = {"type": "object", "properties": {}}
    deferred = True

    def execute(self, input: dict) -> str:
        return "lazy ok"


class OtherLazyTool(LazyTool):
    name = "lazy2"


def test_default_is_direct_load() -> None:
    assert EchoTool.deferred is False
    toolset = ToolSet([EchoTool()])
    assert [t.name for t in toolset.available] == ["echo"]
    assert toolset.deferred == ()


def test_deferred_tools_stay_out_until_activated() -> None:
    toolset = ToolSet([EchoTool(), LazyTool(), OtherLazyTool()])

    assert [t.name for t in toolset.all] == ["echo", "lazy", "lazy2"]
    assert [t.name for t in toolset.deferred] == ["lazy", "lazy2"]
    assert [t.name for t in toolset.available] == ["echo"]
    assert toolset.is_active("echo") and not toolset.is_active("lazy")

    assert toolset.activate("lazy") == ("lazy",)
    assert [t.name for t in toolset.available] == ["echo", "lazy"]  # 顺序同 all
    assert [t.name for t in toolset.deferred] == ["lazy2"]
    assert toolset.is_active("lazy")


def test_activate_is_idempotent_and_ignores_unknown_names() -> None:
    toolset = ToolSet([EchoTool(), LazyTool()])

    assert toolset.activate("lazy", "lazy") == ("lazy",)  # 同一次调用内也去重
    assert toolset.activate("lazy") == ()  # 重复激活无操作
    assert toolset.activate("echo") == ()  # 本就直接加载
    assert toolset.activate("nope") == ()  # 未知名忽略
    assert [t.name for t in toolset.available] == ["echo", "lazy"]


def test_get_reaches_deferred_tools() -> None:
    """执行按名字查全量表:未激活的工具也能被调用(如模型凭历史直接调)。"""
    toolset = ToolSet([LazyTool()])
    tool = toolset.get("lazy")
    assert tool is not None and tool.execute({}) == "lazy ok"
    assert toolset.get("nope") is None
    assert "lazy" in toolset and len(toolset) == 1


def test_duplicate_names_rejected() -> None:
    with pytest.raises(ValueError, match="工具名重复"):
        ToolSet([EchoTool(), EchoTool()])

    toolset = ToolSet([EchoTool()])
    with pytest.raises(ValueError, match="工具名重复"):
        toolset.add(EchoTool())


def test_agent_sends_only_available_tools() -> None:
    model = FakeModel([[make_text_end("ok")], [make_text_end("done")]])
    toolset = ToolSet([EchoTool(), LazyTool()])
    agent = Agent(model=model, tools=toolset, audit=None)

    run_events(agent, "hi")
    assert [t.name for t in model.calls[0]["tools"]] == ["echo"]
    assert set(agent.tools) == {"echo", "lazy"}  # 全量表仍含懒加载工具

    toolset.activate("lazy")
    run_events(agent, "again")
    assert [t.name for t in model.calls[1]["tools"]] == ["echo", "lazy"]


def test_agent_executes_deferred_tool_when_called() -> None:
    model = FakeModel(
        [
            [
                end_event(
                    ToolUseBlock(id="t1", name="lazy", input={}),
                    stop_reason="tool_use",
                )
            ],
            [end_event(TextBlock("done"))],
        ]
    )
    agent = Agent(model=model, tools=ToolSet([LazyTool()]), audit=None)

    events = run_events(agent, "go")
    results = [e for e in events if getattr(e, "content", None) == "lazy ok"]
    assert results and results[0].is_error is False
