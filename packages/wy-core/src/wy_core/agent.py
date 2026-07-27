"""Agent 循环:模型流 → 工具执行 → 结果回填,直到回合结束。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict, dataclass, replace
from typing import cast

from wy_core.log import AuditLog
from wy_core.message import Message, ToolResultBlock, ToolUseBlock, Usage, user_message
from wy_core.model import Model, ModelEnd, ModelError, TextDelta, ThinkingDelta
from wy_core.session import Session
from wy_core.tool import Tool


@dataclass
class ToolCall:
    """工具即将执行。"""

    id: str
    name: str
    input: dict


@dataclass
class ToolResult:
    """工具执行完毕。"""

    id: str
    name: str
    content: str
    is_error: bool


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
    """把 Model、Tool 与 Session 组装为完整 agent。

    审计默认开启:省略 ``audit`` 即写入 CWD/.wy_audit/,显式传
    ``audit=None`` 关闭。单个 Agent 实例不支持并发 ``run``。
    """

    def __init__(
        self,
        *,
        model: Model,
        tools: Sequence[Tool] = (),
        system: str | None = None,
        session: Session | None = None,
        audit: AuditLog | None = _DEFAULT_AUDIT,
        max_iterations: int = 50,
    ) -> None:
        self.model = model
        self.tools = {t.name: t for t in tools}
        if len(self.tools) != len(tools):
            raise ValueError("工具名重复")
        self.system = system
        self.session = session if session is not None else Session()
        self.audit = AuditLog.default() if audit is _DEFAULT_AUDIT else audit
        self.max_iterations = max_iterations
        self._audit("agent_start", {"model": model.name, "tools": list(self.tools)})

    async def run(self, user_input: str) -> AsyncIterator[AgentEvent]:
        """执行一个用户回合,流式产出 AgentEvent,以 TurnEnd 收尾。

        回合内任何异常(含消费方中途关闭流)都会:写一条 error 审计,
        并回滚本回合追加的消息,保持会话处于上一个完整回合的状态;
        本回合已发生过压缩时历史结构已变,跳过回滚。
        """
        checkpoint = len(self.session.messages)
        compacted = False
        try:
            self.session.append(user_message(user_input))
            for _ in range(self.max_iterations):
                if self.session.needs_compaction():
                    info = await self.session.compact(self.model)
                    compacted = True
                    self._audit("compaction", info)
                    yield Compaction(dropped=info["dropped"], summary=info["summary"])

                end = None
                self._audit(
                    "request",
                    {
                        "messages": [m.to_dict() for m in self.session.messages],
                        "system": self.system,
                        "tools": list(self.tools),
                    },
                )
                async for event in self.model.stream(
                    list(self.session.messages),
                    system=self.system,
                    tools=list(self.tools.values()) or None,
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
                    yield TurnEnd(
                        usage=replace(self.session.total_usage),
                        context_tokens=self.session.context_tokens,
                    )
                    return

                results = []
                for block in tool_uses:  # 顺序执行,结果顺序与调用顺序一致
                    yield ToolCall(id=block.id, name=block.name, input=block.input)
                    self._audit(
                        "tool_call", {"id": block.id, "name": block.name, "input": block.input}
                    )
                    content, is_error = await self._execute(block)
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
            raise

    async def _execute(self, block: ToolUseBlock) -> tuple[str, bool]:
        """执行单个工具调用;任何失败都转为错误文本,不打断回合。"""
        tool = self.tools.get(block.name)
        if tool is None:
            return f"Error: unknown tool {block.name}", True
        try:
            return await asyncio.to_thread(tool.execute, block.input), False
        except Exception as exc:  # 工具任意异常都不允许打断回合
            return f"Error: {exc}", True

    def _audit(self, kind: str, data: dict) -> None:
        if self.audit is not None:
            self.audit.write(kind, data)
