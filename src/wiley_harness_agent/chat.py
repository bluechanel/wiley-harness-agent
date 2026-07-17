import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Literal

from openai import OpenAI

from wiley_harness_agent.config import OpenAIConfig


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
    """Manage OpenAI requests and in-memory conversation history."""

    def __init__(self, config: OpenAIConfig) -> None:
        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url)
        self._model = config.model
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
        """Yield reasoning and answer text as it arrives from the provider."""
        self._messages.append({"role": "user", "content": user_input})
        queue: asyncio.Queue[ChatStreamEvent | _StreamError] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(event: ChatStreamEvent | _StreamError) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def produce() -> None:
            try:
                response_stream = self._client.chat.completions.create(
                    model=self._model,
                    messages=self._messages,
                    stream=True,
                )
                for chunk in response_stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    reasoning = _extract_delta_text(
                        delta,
                        ("reasoning_content", "reasoning", "thinking"),
                    )
                    if reasoning:
                        emit(ChatStreamEvent(kind="reasoning", text=reasoning))
                    answer = _extract_delta_text(delta, ("content",))
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
