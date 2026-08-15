"""会话编排:wy-core Agent 流 + 持久会话记录。"""

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import replace

from wy_core import (
    Agent,
    AgentEvent,
    Compaction,
    ThinkingBlock,
    ToolCall,
    ToolHook,
    ToolResult,
    TurnEnd,
    Usage,
)

from wy_coding_agent.reminders import HarnessState, ReminderProvider
from wy_coding_agent.session import SessionRecord, SessionStore


class ConversationService:
    """Coordinate the wy-core agent loop with durable session records."""

    def __init__(
        self,
        agent: Agent,
        store: SessionStore,
        *,
        closer: Callable[[], None] | None = None,
        reminder_providers: Sequence[ReminderProvider] = (),
    ) -> None:
        self._agent = agent
        self._store = store
        self._closer = closer
        self._reminder_providers = tuple(reminder_providers)

    @property
    def plan_mode(self) -> HarnessState | None:
        """harness 状态扩展(现承载 plan 模式);未装配(自定义组装)时为 None。"""
        extension = self._agent.state.get("plan_mode")
        return extension if isinstance(extension, HarnessState) else None

    def set_plan_mode(self, active: bool) -> None:
        """翻转 plan 模式 harness 状态并落盘。

        只改状态——system prompt 由 wy-core 的 ``system_builder`` 在下次提交
        LLM 时按当前状态实时组装(plan 激活含 ``# Plan mode`` 段,否则不含)。
        """
        harness = self.plan_mode
        if harness is None or harness.plan_active == active:
            return
        if active:
            harness.enable_plan()
        else:
            harness.disable_plan()
        self.save_state()

    @property
    def tool_hook(self) -> ToolHook | None:
        """当前装配的工具审批钩子;未装配时为 None。"""
        return self._agent._tool_hook  # noqa: SLF001

    def save_state(self) -> None:
        """状态快照有变化即追加一条 state 记录(回合外的切换也可即时落盘)。"""
        snapshot = self._agent.state.snapshot()
        if snapshot != (self._store.latest_state() or {}):
            self._store.append_state(snapshot)

    def close(self) -> None:
        """Release owned resources (e.g. MCP server connections); idempotent."""
        if self._closer is not None:
            self._closer()

    @property
    def session_id(self) -> str:
        return self._store.session_id

    @property
    def model_name(self) -> str:
        """模型展示名(wy_core.Model.name),供 UI 横幅等展示。"""
        return self._agent.model.name

    @property
    def context_limit(self) -> int:
        """自动压缩阈值(tokens),供 UI 展示距压缩的余量。"""
        return self._agent.session.max_context_tokens

    @property
    def history(self) -> tuple[SessionRecord, ...]:
        return self._store.records

    @property
    def total_usage(self) -> Usage:
        return replace(self._agent.session.total_usage)

    @property
    def last_context_tokens(self) -> int:
        return self._agent.session.context_tokens

    async def stream(self, user_input: str) -> AsyncIterator[AgentEvent]:
        # 轮询动态 reminder:注入本回合 user 消息尾部,并同步落盘 metadata
        # 以便恢复会话时重建模型实际看到的消息(见 SessionStore)。
        reminders = tuple(
            text
            for provider in self._reminder_providers
            if (text := provider.provide()) is not None
        )
        self._store.append_user(user_input, reminders=reminders)
        session = self._agent.session
        start = len(session.messages)  # 本回合消息的起点,落盘文本从这里提取
        before = replace(session.total_usage)

        try:
            async for event in self._agent.run(user_input, reminders=reminders):
                if isinstance(event, ToolCall):
                    self._store.append_tool_call(
                        tool_name=event.name,
                        tool_call_id=event.id,
                        arguments=event.input,
                    )
                elif isinstance(event, ToolResult):
                    self._store.append_tool_output(
                        tool_name=event.name,
                        tool_call_id=event.id,
                        output=event.content,
                        is_error=event.is_error,
                    )
                elif isinstance(event, Compaction):
                    self._store.append_compaction(
                        dropped=event.dropped, summary=event.summary
                    )
                    # 压缩把历史换成 [摘要] + 保留段:重新锚定本回合起点。
                    start = max(1, start - event.dropped + 1)
                elif isinstance(event, TurnEnd):
                    # 权威文本取自本回合组装完成的 assistant 消息
                    # (增量事件只是实时渲染用的装饰,可能不完整)。
                    assistants = [
                        m for m in session.messages[start:] if m.role == "assistant"
                    ]
                    reasoning = "".join(
                        block.thinking
                        for message in assistants
                        for block in message.content
                        if isinstance(block, ThinkingBlock)
                    )
                    answer = "".join(message.text for message in assistants)
                    turn_usage = _subtract(event.usage, before)
                    if reasoning:
                        self._store.append_assistant(
                            reasoning,
                            kind="thinking",
                            usage=turn_usage,
                            total_usage=event.usage,
                        )
                    self._store.append_assistant(
                        answer,
                        kind="answer",
                        usage=turn_usage,
                        total_usage=event.usage,
                        metadata={"context_tokens": event.context_tokens},
                    )
                    # 回合内的状态变化(如 exit_plan_mode)在回合收尾统一落盘。
                    self.save_state()
                yield event
        except Exception as exc:
            self._store.append_assistant(
                str(exc),
                kind="error",
                usage=Usage(),
                total_usage=self.total_usage,
            )
            raise


def _subtract(after: Usage, before: Usage) -> Usage:
    """本回合用量 = 回合结束累计 - 回合开始累计。"""
    return Usage(
        input_tokens=after.input_tokens - before.input_tokens,
        output_tokens=after.output_tokens - before.output_tokens,
        cache_read_tokens=after.cache_read_tokens - before.cache_read_tokens,
        cache_write_tokens=after.cache_write_tokens - before.cache_write_tokens,
    )
