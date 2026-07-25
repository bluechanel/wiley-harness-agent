import asyncio
import copy

from wiley_agent.provider import (
    DoneEvent,
    ProviderUsage,
    ReasoningDelta,
    TextDelta,
    ToolCall,
    UsageEvent,
)
from wiley_agent.prompt_template import BasePromptProvider
from wiley_agent.service import AgentService
from wiley_agent.tools import Tool


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

    agent = AgentService(Provider())  # type: ignore[arg-type]

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
    assert events[2].context_tokens == 5


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
    agent = AgentService(Provider(), instruction="be brief", tools=(echo,))  # type: ignore[arg-type]

    async def collect_events():
        return [event async for event in agent.stream("hello")]

    events = asyncio.run(collect_events())

    assert executed == [{"value": "hi"}]
    assert [event.kind for event in events] == [
        "tool_call",
        "tool_output",
        "answer",
        "usage",
        "done",
    ]
    tool_call_event, tool_output_event = events[0], events[1]
    assert tool_call_event.tool_name == "echo"
    assert tool_call_event.tool_call_id == "call-1"
    assert tool_call_event.tool_arguments == {"value": "hi"}
    assert tool_output_event.tool_name == "echo"
    assert tool_output_event.tool_call_id == "call-1"
    assert tool_output_event.text == "echo:hi"
    assert tool_output_event.tool_is_error is False
    usage_event = events[3]
    assert usage_event.usage is not None
    assert usage_event.usage.input_tokens == 1
    assert usage_event.usage.output_tokens == 1
    # 上下文取最后一轮请求（仅 output_tokens=1），而非整轮累加值。
    assert usage_event.context_tokens == 1
    options = requests[0]["options"]
    # 契约签名只携带会话状态；厂商参数(model/max_tokens/thinking)由实现自持。
    assert set(options) == {"system", "tools"}
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

    agent = AgentService(Provider())  # type: ignore[arg-type]

    async def collect_events():
        return [event async for event in agent.stream("hello")]

    events = asyncio.run(collect_events())

    tool_result = requests[1][-1]["content"][0]
    assert tool_result["tool_use_id"] == "call-1"
    assert "unknown tool" in tool_result["content"]
    assert tool_result["is_error"] is True
    tool_output_event = next(
        event for event in events if event.kind == "tool_output"
    )
    assert tool_output_event.tool_is_error is True
    assert "unknown tool" in tool_output_event.text


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
        Provider(),  # type: ignore[arg-type]
        instruction="be brief",
        prompt_providers=(Section(),),
    )

    async def collect_events():
        return [event async for event in agent.stream("hello")]

    asyncio.run(collect_events())

    assert requests[0]["system"] == "be brief\n\n# Extra\n\nsection"
