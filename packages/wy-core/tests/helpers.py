"""测试辅助:脚本化假模型、简单工具与事件收集器。"""

from __future__ import annotations

import asyncio

from wy_core import Agent, Block, Message, Model, ModelEnd, ModelEvent, TextBlock, Tool, ToolApproval, ToolCall, ToolHook, Usage


def end_event(
    *blocks: Block, usage: Usage | None = None, stop_reason: str = "end_turn"
) -> ModelEnd:
    """构造一个 ModelEnd:assistant 消息由 blocks 组成。"""
    return ModelEnd(
        message=Message(role="assistant", content=list(blocks)),
        usage=usage or Usage(input_tokens=10, output_tokens=5),
        stop_reason=stop_reason,
    )


class FakeModel(Model):
    """按脚本逐次吐出事件组,并记录每次收到的请求供断言。"""

    name = "fake"

    def __init__(self, scripts: list[list[ModelEvent]]) -> None:
        self.scripts = list(scripts)
        self.calls: list[dict] = []

    async def stream(self, messages, *, system=None, tools=None):
        self.calls.append({"messages": list(messages), "system": system, "tools": tools})
        for event in self.scripts.pop(0):
            if isinstance(event, BaseException):  # 脚本里放异常即在流中抛出
                raise event
            yield event


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


def run_events(agent: Agent, prompt: str, **kwargs) -> list:
    """同步跑完一个回合,收集全部 AgentEvent(仓库测试不引入 pytest-asyncio)。"""

    async def go() -> list:
        return [event async for event in agent.run(prompt, **kwargs)]

    return asyncio.run(go())


def make_text_end(text: str, **kwargs) -> ModelEnd:
    return end_event(TextBlock(text), **kwargs)


class AllowAllHook(ToolHook):
    """测试用:批准所有工具调用。"""

    async def approve(self, call: ToolCall) -> ToolApproval:
        return ToolApproval(allowed=True, reason="ok")


class DenyAllHook(ToolHook):
    """测试用:拒绝所有工具调用。"""

    async def approve(self, call: ToolCall) -> ToolApproval:
        return ToolApproval(allowed=False, reason="测试拒绝")


class DenyByNameHook(ToolHook):
    """测试用:拒绝特定名称的工具调用。"""

    def __init__(self, denied_names: set[str]) -> None:
        self.denied_names = denied_names

    async def approve(self, call: ToolCall) -> ToolApproval:
        if call.name in self.denied_names:
            return ToolApproval(allowed=False, reason=f"禁止调用 {call.name}")
        return ToolApproval(allowed=True)


class BoomHook(ToolHook):
    """测试用:approve 总是抛异常。"""

    async def approve(self, call: ToolCall) -> ToolApproval:
        raise RuntimeError("审批服务挂了")
