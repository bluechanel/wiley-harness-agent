import asyncio

from wiley_harness_agent.agent.chat import ChatService
from wiley_harness_agent.agent.config import AnthropicConfig
from wiley_harness_agent.agent.provider import (
    DoneEvent,
    ProviderUsage,
    ReasoningDelta,
    TextDelta,
    UsageEvent,
)


def test_chat_stream_accumulates_new_content_blocks() -> None:
    class Provider:
        async def stream_request(self, messages, **options):
            yield UsageEvent(ProviderUsage(input_tokens=3))
            yield ReasoningDelta("think", index=0)
            yield TextDelta("answer", index=1)
            yield UsageEvent(
                ProviderUsage(output_tokens=2), stop_reason="end_turn"
            )
            yield DoneEvent()

    chat = ChatService(
        AnthropicConfig(
            api_key="key",
            base_url="https://example.com",
            model="model",
            thinking_budget_tokens=0,
        )
    )
    chat._provider = Provider()  # type: ignore[assignment]

    async def collect_events():
        return [event async for event in chat.stream("hello")]

    events = asyncio.run(collect_events())

    assert [(event.kind, event.text) for event in events] == [
        ("reasoning", "think"),
        ("answer", "answer"),
        ("usage", ""),
        ("done", ""),
    ]
    assert events[2].usage is not None
    assert events[2].usage.input_tokens == 3
    assert events[2].usage.output_tokens == 2
