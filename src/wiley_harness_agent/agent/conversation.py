from typing import AsyncIterator, Callable

from wiley_harness_agent.agent.service import AgentService, ChatStreamEvent
from wiley_harness_agent.agent.session import SessionRecord, SessionStore
from wiley_harness_agent.agent.usage import ChatUsage


class ConversationService:
    """Coordinate model streaming with durable session records."""

    def __init__(
        self,
        agent: AgentService,
        session: SessionStore,
        *,
        closer: Callable[[], None] | None = None,
    ) -> None:
        self._agent = agent
        self._session = session
        self._closer = closer

    def close(self) -> None:
        """Release owned resources (e.g. MCP server connections); idempotent."""
        if self._closer is not None:
            self._closer()

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def history(self) -> tuple[SessionRecord, ...]:
        return self._session.records

    @property
    def total_usage(self) -> ChatUsage:
        return self._session.total_usage

    @property
    def last_context_tokens(self) -> int:
        return self._session.last_context_tokens

    async def stream(self, user_input: str) -> AsyncIterator[ChatStreamEvent]:
        self._session.append_user(user_input)
        reasoning_parts: list[str] = []
        answer_parts: list[str] = []
        usage = ChatUsage()
        total_usage = self._session.total_usage
        context_tokens = 0

        try:
            async for event in self._agent.stream(user_input):
                if event.kind == "reasoning":
                    reasoning_parts.append(event.text)
                elif event.kind == "answer":
                    answer_parts.append(event.text)
                elif event.kind == "tool_call":
                    self._session.append_tool_call(
                        tool_name=event.tool_name,
                        tool_call_id=event.tool_call_id,
                        arguments=event.tool_arguments,
                    )
                elif event.kind == "tool_output":
                    self._session.append_tool_output(
                        tool_name=event.tool_name,
                        tool_call_id=event.tool_call_id,
                        output=event.text,
                        is_error=event.tool_is_error,
                    )
                elif event.kind == "usage":
                    usage = event.usage or ChatUsage()
                    total_usage = event.total_usage or total_usage
                    context_tokens = event.context_tokens
                elif event.kind == "done":
                    reasoning = "".join(reasoning_parts)
                    answer = "".join(answer_parts)
                    if reasoning:
                        self._session.append_assistant(
                            reasoning,
                            kind="thinking",
                            usage=usage,
                            total_usage=total_usage,
                        )
                    self._session.append_assistant(
                        answer,
                        kind="answer",
                        usage=usage,
                        total_usage=total_usage,
                        metadata={"context_tokens": context_tokens},
                    )
                yield event
        except Exception as exc:
            self._session.append_assistant(
                str(exc),
                kind="error",
                usage=ChatUsage(),
                total_usage=self._session.total_usage,
            )
            raise

