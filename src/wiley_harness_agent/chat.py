import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Literal

import anthropic

from wiley_harness_agent.config import AnthropicConfig


@dataclass(frozen=True, slots=True)
class ChatResult:
    answer: str
    reasoning: str | None = None


@dataclass(frozen=True, slots=True)
class ChatStreamEvent:
    kind: Literal["reasoning", "answer", "done"]
    text: str = ""


@dataclass(frozen=True, slots=True)
class _StreamError:
    error: BaseException


def _extract_delta_text(delta: object, fields: tuple[str, ...]) -> str | None:
    for field in fields:
        value = getattr(delta, field, None)
        if isinstance(value, str) and value:
            return value
    return None


class ChatService:
    """Manage Anthropic requests and in-memory conversation history."""

    def __init__(self, config: AnthropicConfig) -> None:
        self._client = anthropic.Anthropic(
            api_key=config.api_key,
            base_url=config.base_url,
        )
        self._model = config.model
        self._max_tokens = config.max_tokens
        self._thinking_budget_tokens = config.thinking_budget_tokens
        self._messages: list[dict[str, str]] = []

    async def send(self, user_input: str) -> ChatResult:
        answer_parts: list[str] = []
        reasoning_parts: list[str] = []

        async for event in self.stream(user_input):
            if event.kind == "answer":
                answer_parts.append(event.text)
            elif event.kind == "reasoning":
                reasoning_parts.append(event.text)

        return ChatResult(
            answer="".join(answer_parts),
            reasoning="".join(reasoning_parts) or None,
        )

    async def stream(self, user_input: str) -> AsyncIterator[ChatStreamEvent]:
        """Yield thinking and answer text as it arrives from Anthropic."""
        self._messages.append({"role": "user", "content": user_input})
        queue: asyncio.Queue[ChatStreamEvent | _StreamError] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(event: ChatStreamEvent | _StreamError) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def produce() -> None:
            try:
                request: dict[str, object] = {
                    "model": self._model,
                    "messages": self._messages,
                    "max_tokens": self._max_tokens,
                    "stream": True,
                }
                if self._thinking_budget_tokens:
                    request["thinking"] = {
                        "type": "enabled",
                        "budget_tokens": self._thinking_budget_tokens,
                    }

                response_stream = self._client.messages.create(**request)
                for event in response_stream:
                    if getattr(event, "type", None) != "content_block_delta":
                        continue
                    delta = event.delta
                    if getattr(delta, "type", None) == "thinking_delta":
                        thinking = _extract_delta_text(delta, ("thinking",))
                        if thinking:
                            emit(ChatStreamEvent(kind="reasoning", text=thinking))
                    elif getattr(delta, "type", None) == "text_delta":
                        answer = _extract_delta_text(delta, ("text",))
                        if answer:
                            emit(ChatStreamEvent(kind="answer", text=answer))
                emit(ChatStreamEvent(kind="done"))
            except BaseException as exc:
                emit(_StreamError(exc))

        producer = asyncio.create_task(asyncio.to_thread(produce))
        answer_parts: list[str] = []

        try:
            while True:
                event = await queue.get()
                if isinstance(event, _StreamError):
                    await producer
                    raise event.error
                if event.kind == "done":
                    await producer
                    self._messages.append(
                        {"role": "assistant", "content": "".join(answer_parts)}
                    )
                    yield event
                    return
                if event.kind == "answer":
                    answer_parts.append(event.text)
                yield event
        except BaseException:
            self._messages.pop()
            raise
