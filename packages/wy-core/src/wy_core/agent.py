"""Agent 循环:模型流 → 工具执行 → 结果回填,直到回合结束。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import asdict, dataclass, replace
from typing import cast

from wy_core.log import AuditLog
from wy_core.message import Message, ToolResultBlock, ToolUseBlock, Usage, user_message
from wy_core.model import Model, ModelEnd, ModelError, TextDelta, ThinkingDelta
from wy_core.session import Session
from wy_core.state import AgentState
from wy_core.tool import Tool, ToolApproval, ToolCall, ToolHook, ToolResult
from wy_core.toolset import ToolSet


@dataclass
class Compaction:
    """上下文压缩已发生。"""

    dropped: int
    summary: str


@dataclass
class TurnEnd:
    """回合结束:累计用量与当前上下文规模。"""

    usage: Usage
    context_tokens: int


AgentEvent = TextDelta | ThinkingDelta | ToolCall | ToolResult | Compaction | TurnEnd


class AgentError(RuntimeError):
    """agent 循环自身的失败,如超过最大迭代轮数。"""


_DEFAULT_AUDIT = cast(AuditLog, object())  # 哨兵:区分"未传 audit"与显式 None


class Agent:
    """把 Model、Tool 与 AgentState(Session + 状态扩展)组装为完整 agent。

    ``state``/``session`` 只能传其一:只传 ``session``(或都不传)时内部
    包装为无扩展的 ``AgentState``,``agent.session`` 始终是
    ``state.session`` 的兼容别名。``tools`` 可传工具序列或 ``ToolSet``:
    传序列时内部包装为 ``ToolSet``,每轮请求只发 ``ToolSet.available``
    (直接加载的工具 + 已激活的懒加载工具),执行仍按名字在全量表里查。
    审计默认开启:省略 ``audit`` 即写入 CWD/.wy_audit/,显式传
    ``audit=None`` 关闭。单个 Agent 实例不支持并发 ``run``。

    ``system`` 是静态 system prompt;``system_builder`` 非 None 时每次模型
    请求(``run`` 循环内每轮 ``model.stream``)前实时调用它取当前 system,
    供调用方按可变 harness 状态组装(无 builder 时回落静态 ``system``)。
    """

    def __init__(
        self,
        *,
        model: Model,
        tools: Sequence[Tool] | ToolSet = (),
        system: str | None = None,
        system_builder: Callable[[], str | None] | None = None,
        session: Session | None = None,
        state: AgentState | None = None,
        audit: AuditLog | None = _DEFAULT_AUDIT,
        max_iterations: int = 50,
        tool_hook: ToolHook | None = None,
    ) -> None:
        self.model = model
        self.toolset = tools if isinstance(tools, ToolSet) else ToolSet(tools)
        self.system = system
        self.system_builder = system_builder
        if state is not None and session is not None:
            raise ValueError("session 与 state 只能传其一")
        self.state = state if state is not None else AgentState(session=session)
        self.session = self.state.session  # 兼容别名,与 state.session 恒为同一对象
        self.audit = AuditLog.default() if audit is _DEFAULT_AUDIT else audit
        self.max_iterations = max_iterations
        self._tool_hook = tool_hook
        self._audit(
            "agent_start",
            {
                "model": model.name,
                "tools": [t.name for t in self.toolset.all],
                "deferred_tools": [t.name for t in self.toolset.deferred],
                "state": list(self.state.extensions),
            },
        )

    @property
    def tools(self) -> dict[str, Tool]:
        """全量工具表(含未激活的懒加载工具),按名字索引。"""
        return {tool.name: tool for tool in self.toolset.all}

    def _current_system(self) -> str | None:
        """按当前 harness 状态计算 system;无 builder 时回落静态 system。"""
        return self.system_builder() if self.system_builder is not None else self.system

    async def run(
        self, user_input: str, *, reminders: Sequence[str] = ()
    ) -> AsyncIterator[AgentEvent]:
        """执行一个用户回合,流式产出 AgentEvent,以 TurnEnd 收尾。

        reminders 作为 ``<system-reminder>`` 文本块追加在本回合 user 消息
        尾部(见 ``user_message``),供调用方注入模式、通知等动态状态。

        回合内任何异常(含消费方中途关闭流)都会:写一条 error 审计,
        并回滚本回合追加的消息,保持会话处于上一个完整回合的状态;
        本回合已发生过压缩时历史结构已变,跳过回滚。
        """
        checkpoint = len(self.session.messages)
        compacted = False
        try:
            self.state.turn_start()
            self.session.append(user_message(user_input, reminders=reminders))
            for _ in range(self.max_iterations):
                if self.session.needs_compaction():
                    info = await self.session.compact(self.model)
                    compacted = True
                    self.state.compaction(info["dropped"])
                    self._audit("compaction", info)
                    yield Compaction(dropped=info["dropped"], summary=info["summary"])

                end = None
                available = list(self.toolset.available)
                self._audit(
                    "request",
                    {
                        "messages": [m.to_dict() for m in self.session.messages],
                        "system": self._current_system(),
                        "tools": [t.name for t in available],
                    },
                )
                async for event in self.model.stream(
                    list(self.session.messages),
                    system=self._current_system(),
                    tools=available or None,
                ):
                    if isinstance(event, ModelEnd):
                        end = event
                    else:
                        yield event
                if end is None:
                    raise ModelError("模型流未产出 ModelEnd")

                self.session.append(end.message)
                self.session.record_usage(end.usage)
                self._audit(
                    "model_end",
                    {
                        "message": end.message.to_dict(),
                        "usage": asdict(end.usage),
                        "stop_reason": end.stop_reason,
                    },
                )

                tool_uses = [b for b in end.message.content if isinstance(b, ToolUseBlock)]
                if end.stop_reason != "tool_use" or not tool_uses:
                    self.state.turn_end()
                    yield TurnEnd(
                        usage=replace(self.session.total_usage),
                        context_tokens=self.session.context_tokens,
                    )
                    return

                for block in tool_uses:
                    yield ToolCall(id=block.id, name=block.name, input=block.input)
                    self._audit(
                        "tool_call", {"id": block.id, "name": block.name, "input": block.input}
                    )
                # 并发执行全部调用;事件与回填顺序仍与调用顺序一致
                outcomes = await asyncio.gather(*(self._execute(b) for b in tool_uses))
                results = []
                for block, (content, is_error) in zip(tool_uses, outcomes):
                    yield ToolResult(id=block.id, name=block.name, content=content, is_error=is_error)
                    self._audit(
                        "tool_result",
                        {"id": block.id, "name": block.name, "content": content, "is_error": is_error},
                    )
                    results.append(
                        ToolResultBlock(tool_use_id=block.id, content=content, is_error=is_error)
                    )
                self.session.append(Message(role="user", content=results))

            raise AgentError(f"超过最大迭代轮数 {self.max_iterations}")
        except BaseException as exc:
            self._audit("error", {"type": type(exc).__name__, "error": str(exc)})
            if not compacted:
                del self.session.messages[checkpoint:]
            self.state.rollback()
            raise

    async def _execute(self, block: ToolUseBlock) -> tuple[str, bool]:
        """执行单个工具调用;任何失败都转为错误文本,不打断回合。"""
        if self._tool_hook is not None:
            call = ToolCall(id=block.id, name=block.name, input=block.input)
            try:
                decision = await self._tool_hook.approve(call)
            except Exception as exc:
                decision = ToolApproval(allowed=False, reason=str(exc))
            self._audit(
                "tool_approval",
                {
                    "id": block.id,
                    "name": block.name,
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                },
            )
            if not decision.allowed:
                return f"工具调用被拒绝: {decision.reason or '未说明原因'}", True
        tool = self.toolset.get(block.name)
        if tool is None:
            return f"Error: unknown tool {block.name}", True
        try:
            return await asyncio.to_thread(tool.execute, block.input), False
        except Exception as exc:  # 工具任意异常都不允许打断回合
            return f"Error: {exc}", True

    def _audit(self, kind: str, data: dict) -> None:
        if self.audit is not None:
            self.audit.write(kind, data)
