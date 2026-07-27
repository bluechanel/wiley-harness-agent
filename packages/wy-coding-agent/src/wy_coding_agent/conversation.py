"""会话编排:wy-core Agent 流 + 持久会话记录。"""

from collections.abc import AsyncIterator, Callable
from dataclasses import replace

from wy_core import (
    Agent,
    AgentEvent,
    Compaction,
    ThinkingBlock,
    ToolCall,
    ToolResult,
    TurnEnd,
    Usage,
)

from wy_coding_agent.session import SessionRecord, SessionStore


class ConversationService:
    """Coordinate the wy-core agent loop with durable session records."""

    def __init__(
        self,
        agent: Agent,
        store: SessionStore,
        *,
        closer: Callable[[], None] | None = None,
    ) -> None:
        self._agent = agent
        self._store = store
        self._closer = closer

    def close(self) -> None:
        """Release owned resources (e.g. MCP server connections); idempotent."""
        if self._closer is not None:
            self._closer()

    @property
    def session_id(self) -> str:
        return self._store.session_id

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
        self._store.append_user(user_input)
        session = self._agent.session
        start = len(session.messages)  # 本回合消息的起点,落盘文本从这里提取
        before = replace(session.total_usage)

        try:
            async for event in self._agent.run(user_input):
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
