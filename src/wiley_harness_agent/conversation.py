from typing import Any, AsyncIterator

from wiley_harness_agent.chat import ChatService, ChatStreamEvent
from wiley_harness_agent.session import SessionRecord, SessionStore
from wiley_harness_agent.usage import ChatUsage


class ConversationService:
    """Coordinate model streaming with durable session records."""

    def __init__(self, chat: ChatService, session: SessionStore) -> None:
        self._chat = chat
        self._session = session

    @property
    def session_id(self) -> str:
        return self._session.session_id

    @property
    def history(self) -> tuple[SessionRecord, ...]:
        return self._session.records

    async def stream(self, user_input: str) -> AsyncIterator[ChatStreamEvent]:
        self._session.append_user(user_input)
        reasoning_parts: list[str] = []
        answer_parts: list[str] = []
        usage = ChatUsage()
        total_usage = self._session.total_usage

        try:
            async for event in self._chat.stream(user_input):
                if event.kind == "reasoning":
                    reasoning_parts.append(event.text)
                elif event.kind == "answer":
                    answer_parts.append(event.text)
                elif event.kind == "usage":
                    usage = event.usage or ChatUsage()
                    total_usage = event.total_usage or total_usage
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

    def record_tool_call(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        arguments: Any,
    ) -> SessionRecord:
        return self._session.append_tool_call(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            arguments=arguments,
        )

    def record_tool_output(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        output: Any,
    ) -> SessionRecord:
        return self._session.append_tool_output(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            output=output,
        )

