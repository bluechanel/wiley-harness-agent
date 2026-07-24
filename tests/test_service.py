import asyncio
import copy

from wiley_harness_agent.agent.config import AnthropicConfig
from wiley_harness_agent.agent.provider import (
    DoneEvent,
    ProviderUsage,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    UsageEvent,
)
from wiley_harness_agent.agent.prompt_template import BasePromptProvider
from wiley_harness_agent.agent.service import AgentService
from wiley_harness_agent.agent.tools import Tool


def _config() -> AnthropicConfig:
    return AnthropicConfig(
        api_key="key",
        base_url="https://example.com",
        model="model",
        thinking_budget_tokens=0,
    )


def test_agent_stream_accumulates_new_content_blocks() -> None:
    class Provider:
        async def stream_request(self, messages, **options):
            yield UsageEvent(ProviderUsage(input_tokens=3))
            yield ReasoningDelta("think", index=0)
            yield TextDelta("answer", index=1)
            yield UsageEvent(
                ProviderUsage(output_tokens=2), stop_reason="end_turn"
            )
            yield DoneEvent()

    agent = AgentService(_config())
    agent._provider = Provider()  # type: ignore[assignment]

    async def collect_events():
        return [event async for event in agent.stream("hello")]

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


def test_agent_service_runs_registered_tools() -> None:
    requests: list[dict] = []

    class Provider:
        async def stream_request(self, messages, **options):
            requests.append(
                {"messages": copy.deepcopy(messages), "options": dict(options)}
            )
            if len(requests) == 1:
                yield ToolCall(
                    index=0,
                    tool_call_id="call-1",
                    name="echo",
                    input_json='{"value": "hi"}',
                )
                yield UsageEvent(ProviderUsage(input_tokens=1), stop_reason="tool_use")
            else:
                yield TextDelta("done", index=0)
                yield UsageEvent(ProviderUsage(output_tokens=1), stop_reason="end_turn")
            yield DoneEvent()

    executed: list[dict] = []

    def run_echo(arguments):
        executed.append(dict(arguments))
        return f"echo:{arguments['value']}"

    echo = Tool(
        definition={
            "name": "echo",
            "description": "Echo the value back.",
            "input_schema": {"type": "object"},
        },
        execute=run_echo,
    )
    agent = AgentService(_config(), instruction="be brief", tools=(echo,))
    agent._provider = Provider()  # type: ignore[assignment]

    async def collect_events():
        return [event async for event in agent.stream("hello")]

    events = asyncio.run(collect_events())

    assert executed == [{"value": "hi"}]
    assert [event.kind for event in events] == ["answer", "usage", "done"]
    options = requests[0]["options"]
    assert options["model"] == "model"
    assert options["max_tokens"] == 8192
    assert "thinking" not in options
    assert options["system"] == "be brief"
    assert options["tools"] == [
        {
            "name": "echo",
            "description": "Echo the value back.",
            "input_schema": {"type": "object"},
        }
    ]
    assert requests[1]["messages"][-1] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "call-1", "content": "echo:hi"}
        ],
    }


def test_agent_service_reports_unknown_tool_as_error_result() -> None:
    requests: list[list] = []

    class Provider:
        async def stream_request(self, messages, **options):
            requests.append(copy.deepcopy(messages))
            if len(requests) == 1:
                yield ToolCall(index=0, tool_call_id="call-1", name="missing")
                yield UsageEvent(ProviderUsage(), stop_reason="tool_use")
            else:
                yield TextDelta("ok", index=0)
                yield UsageEvent(ProviderUsage(), stop_reason="end_turn")
            yield DoneEvent()

    agent = AgentService(_config())
    agent._provider = Provider()  # type: ignore[assignment]

    async def collect_events():
        return [event async for event in agent.stream("hello")]

    asyncio.run(collect_events())

    tool_result = requests[1][-1]["content"][0]
    assert tool_result["tool_use_id"] == "call-1"
    assert "unknown tool" in tool_result["content"]
    assert tool_result["is_error"] is True


def test_agent_service_composes_system_prompt_from_providers() -> None:
    requests: list[dict] = []

    class Provider:
        async def stream_request(self, messages, **options):
            requests.append(dict(options))
            yield TextDelta("ok", index=0)
            yield UsageEvent(ProviderUsage(), stop_reason="end_turn")
            yield DoneEvent()

    class Section(BasePromptProvider):
        def provide(self) -> str | None:
            return "# Extra\n\nsection"

    agent = AgentService(
        _config(), instruction="be brief", prompt_providers=(Section(),)
    )
    agent._provider = Provider()  # type: ignore[assignment]

    async def collect_events():
        return [event async for event in agent.stream("hello")]

    asyncio.run(collect_events())

    assert requests[0]["system"] == "be brief\n\n# Extra\n\nsection"
