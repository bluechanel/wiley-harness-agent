import asyncio

import pytest

from wiley_agent import (
    DoneEvent,
    ErrorEvent,
    ProviderError,
    ProviderUsage,
    RedactedReasoning,
    ReasoningDelta,
    TextDelta,
    ThinkingSignature,
    ToolCall,
    ToolResult,
    UsageEvent,
)
from wiley_agent.provider.anthropic import AnthropicProvider, from_event


def _install_fake_transport(
    monkeypatch: pytest.MonkeyPatch, lines: tuple[bytes, ...]
) -> dict[str, object]:
    """Replace aiohttp.ClientSession with a canned-SSE fake; return captures."""
    captured: dict[str, object] = {}

    class Content:
        def __aiter__(self):
            async def iterate():
                for line in lines:
                    yield line

            return iterate()

    class Response:
        status = 200
        content = Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return Response()

    monkeypatch.setattr(
        "wiley_agent.provider.anthropic.aiohttp.ClientSession",
        lambda **kwargs: Session(),
    )
    return captured


def _collect(provider: AnthropicProvider, **request):
    async def collect_events():
        return [
            event
            async for event in provider.stream_request(
                [{"role": "user", "content": "hello"}], **request
            )
        ]

    return asyncio.run(collect_events())


def test_anthropic_provider_decodes_sse_and_builds_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_transport(
        monkeypatch,
        (
            b"event: message_start\n",
            b'data: {"type":"message_start","message":{"usage":{"input_tokens":12}}}\n',
            b"\n",
            b'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"why"}}\n',
            b"\n",
            b'data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"hello"}}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
        ),
    )
    provider = AnthropicProvider(
        api_key=" config-key ",
        base_url="https://api.anthropic.com/",
        model="claude-test",
        max_tokens=2048,
        thinking_budget_tokens=1024,
    )

    events = _collect(
        provider,
        system="be brief",
        tools=[{"name": "echo", "input_schema": {"type": "object"}}],
    )

    assert events == [
        UsageEvent(ProviderUsage(input_tokens=12)),
        ReasoningDelta("why", index=0),
        TextDelta("hello", index=1),
        DoneEvent(),
    ]
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["json"] == {
        "model": "claude-test",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "system": "be brief",
        "tools": [{"name": "echo", "input_schema": {"type": "object"}}],
    }
    assert captured["headers"]["x-api-key"] == "config-key"


def test_anthropic_provider_omits_optional_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_transport(monkeypatch, (b"data: [DONE]\n", b"\n"))
    provider = AnthropicProvider(
        api_key="key",
        base_url="https://api.anthropic.com",
        model="claude-test",
        max_tokens=2048,
        thinking_budget_tokens=0,
    )

    assert _collect(provider) == [DoneEvent()]
    assert captured["json"] == {
        "model": "claude-test",
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }


def test_anthropic_provider_exposes_model_identity() -> None:
    provider = AnthropicProvider(
        api_key="key", base_url="https://api.anthropic.com", model=" claude-test "
    )
    assert provider.model == "claude-test"


def test_anthropic_provider_requires_api_key() -> None:
    with pytest.raises(ProviderError, match="API key"):
        AnthropicProvider(
            api_key="", base_url="https://api.anthropic.com", model="claude-test"
        )


def test_anthropic_provider_requires_base_url() -> None:
    with pytest.raises(ProviderError, match="base URL"):
        AnthropicProvider(api_key="key", base_url="", model="claude-test")


def test_anthropic_provider_requires_model() -> None:
    with pytest.raises(ProviderError, match="model"):
        AnthropicProvider(
            api_key="key", base_url="https://api.anthropic.com", model=" "
        )


def test_anthropic_event_parser_handles_tools_errors_and_usage() -> None:
    assert from_event(
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {
                "type": "tool_use",
                "id": "call-1",
                "name": "edit",
                "input": {},
            },
        }
    ) == ToolCall(index=2, tool_call_id="call-1", name="edit", input_json="{}")
    assert from_event(
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": '{"path":'},
        }
    ) == ToolCall(index=2, input_json='{"path":')
    assert from_event(
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "signature_delta", "signature": "sig"},
        }
    ) == ThinkingSignature("sig", index=2)
    assert from_event(
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {"type": "redacted_thinking", "data": "opaque"},
        }
    ) == RedactedReasoning("opaque", index=1)
    assert from_event(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_result",
                "tool_use_id": "call-1",
                "content": "ok",
            },
        }
    ) == ToolResult(tool_call_id="call-1", content="ok")
    assert from_event(
        {"type": "error", "error": {"type": "overloaded", "message": "busy"}}
    ) == ErrorEvent(message="busy", code="overloaded")
    assert from_event(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 7},
        }
    ) == UsageEvent(ProviderUsage(output_tokens=7), stop_reason="tool_use")
    assert from_event({"type": "ping"}) is None
