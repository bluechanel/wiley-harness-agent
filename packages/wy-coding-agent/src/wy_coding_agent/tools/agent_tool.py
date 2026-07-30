"""Agent 工具执行器:把自包含子任务派发给独立上下文的子 agent。

与 ``mcp_tool``/``skill`` 同型:本模块只有类、无模块级实例,自动扫描不
收录;由 factory 在完整工具集与 system prompt 就绪后构造。子 agent 复用
父 agent 的 Model 与工具实例(要求 Model 实现不持有事件循环绑定状态,
如 ``AnthropicModel`` 每次 stream 新建客户端;bash 会话随实例与父共享),
工具集取追加本工具之前的快照——构造上杜绝嵌套派生。每次派生在全新
``wy_core.Session`` 中跑完整循环,``execute`` 由 wy-core 放在工具线程,
线程内无事件循环,故以 ``asyncio.run`` 驱动;最终一条 assistant 消息的
正文即工具结果,中间事件全部丢弃(黑盒展示)。审计每次派生独立成文件
``<audit_base>.sub-<短id>.audit.jsonl``,与主审计同目录。
"""

import asyncio
import uuid
from collections.abc import Sequence
from pathlib import Path

from wy_core import Agent, AuditLog, Model, Session, Tool

_MAX_RESULT_CHARS = 30_000  # 结果上限,对齐 bash/grep 的输出截断惯例

_TASK_TRAILER = (
    "\n\n[This task was delegated to you by another agent. Your final reply "
    "is returned to it verbatim as the only result, so finish with a "
    "complete, self-contained answer.]"
)


class AgentTool(Tool):
    """Spawn a sub-agent that handles a self-contained task in a fresh context."""

    name = "agent"
    description = (
        "Spawn a sub-agent to handle a self-contained sub-task in a fresh "
        "context window. The sub-agent has the same tools as you (except "
        "this one) but none of your conversation history.\n"
        "- Use it for researching or exploring the codebase, and for "
        "multi-step sub-tasks whose intermediate output would pollute "
        "your context\n"
        "- The task must be self-contained: include all needed background, "
        "file paths and the expected deliverable\n"
        "- Only the sub-agent's final reply is returned; it cannot see your "
        "conversation or ask follow-up questions"
    )
    parameters = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": (
                    "Complete, self-contained description of the sub-task, "
                    "including background, relevant paths and the expected "
                    "deliverable."
                ),
            },
        },
        "required": ["task"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        model: Model,
        tools: Sequence[Tool],
        system: str | None = None,
        audit_base: Path | None = None,
        max_context_tokens: int = 150_000,
        keep_recent: int = 8,
        max_iterations: int = 25,
    ) -> None:
        self._model = model
        self._tools = tuple(tools)
        self._system = system
        self._audit_base = audit_base
        self._max_context_tokens = max_context_tokens
        self._keep_recent = keep_recent
        self._max_iterations = max_iterations

    def execute(self, input: dict) -> str:
        task = str(input.get("task", "")).strip()
        if not task:
            raise ValueError("task is empty; describe the sub-task to delegate")
        return asyncio.run(self._run(task))

    async def _run(self, task: str) -> str:
        audit = self._open_audit()
        session = Session(
            max_context_tokens=self._max_context_tokens,
            keep_recent=self._keep_recent,
        )
        try:
            agent = Agent(
                model=self._model,
                tools=self._tools,
                system=self._system,
                session=session,
                audit=audit,
                max_iterations=self._max_iterations,
            )
            async for _ in agent.run(task + _TASK_TRAILER):
                pass  # 黑盒:增量与工具事件不透出,权威结果取自会话消息
        finally:
            if audit is not None:
                audit.close()
        assistants = [m for m in session.messages if m.role == "assistant"]
        text = assistants[-1].text.strip() if assistants else ""
        if not text:
            return "(sub-agent finished without a final text reply)"
        if len(text) > _MAX_RESULT_CHARS:
            text = text[:_MAX_RESULT_CHARS] + "\n... (sub-agent output truncated)"
        return text

    def _open_audit(self) -> AuditLog | None:
        if self._audit_base is None:
            return None
        base = self._audit_base
        name = f"{base.name}.sub-{uuid.uuid4().hex[:8]}.audit.jsonl"
        return AuditLog(base.parent / name)
