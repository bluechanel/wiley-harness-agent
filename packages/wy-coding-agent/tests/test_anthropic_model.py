"""AnthropicModel:SDK 流式接入、请求体构造与完整消息组装(wy-core 契约)。"""

import asyncio
import json

import anthropic as anthropic_sdk
import httpx
import pytest

from wy_core import (
    Message,
    ModelEnd,
    ModelError,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    user_message,
)

from wy_coding_agent.anthropic import AnthropicModel, RedactedThinkingBlock

from app_helpers import EchoTool


def _sse(*events: dict) -> bytes:
    """把事件字典序列编码为 Anthropic SSE wire 格式。"""
    return "".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
    ).encode()


def _message_start(input_tokens: int = 0, output_tokens: int = 0) -> dict:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
    }


def _install_fake_transport(
    monkeypatch: pytest.MonkeyPatch, body: bytes, *, status: int = 200
) -> dict[str, object]:
    """让被测模块的 AsyncAnthropic 走 httpx.MockTransport;返回请求捕获。"""
    captured: dict[str, object] = {}
    headers = (
        {"content-type": "text/event-stream"}
        if status < 400
        else {"content-type": "application/json"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["json"] = json.loads(request.content)
        return httpx.Response(status, headers=headers, content=body)

    def factory(**kwargs):
        return anthropic_sdk.AsyncAnthropic(
            **kwargs,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr("wy_coding_agent.anthropic.AsyncAnthropic", factory)
    return captured


def _model(**overrides) -> AnthropicModel:
    params = dict(
        api_key="key",
        base_url="https://api.anthropic.com",
        model="claude-test",
        max_tokens=2048,
        thinking_budget_tokens=1024,
    )
    params.update(overrides)
    return AnthropicModel(**params)


def _collect(model: AnthropicModel, messages=None, **request):
    async def collect_events():
        return [
            event
            async for event in model.stream(
                messages if messages is not None else [user_message("hello")], **request
            )
        ]

    return asyncio.run(collect_events())


def test_stream_decodes_sse_builds_body_and_assembles_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_transport(
        monkeypatch,
        _sse(
            _message_start(input_tokens=12, output_tokens=1),
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": "", "signature": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "why"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "sig"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "hello"},
            },
            {"type": "content_block_stop", "index": 1},
            {
                "type": "content_block_start",
                "index": 2,
                "content_block": {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "echo",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": '{"text":'},
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": '"hi"}'},
            },
            {"type": "content_block_stop", "index": 2},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 7},
            },
            {"type": "message_stop"},
        ),
    )
    model = _model(api_key=" config-key ", base_url="https://api.anthropic.com/")

    events = _collect(model, system="be brief", tools=[EchoTool()])

    assert events[:2] == [ThinkingDelta("why"), TextDelta("hello")]
    end = events[2]
    assert isinstance(end, ModelEnd)
    assert end.message.content == [
        ThinkingBlock(thinking="why", signature="sig"),
        TextBlock("hello"),
        ToolUseBlock(id="call-1", name="echo", input={"text": "hi"}),
    ]
    assert end.usage == Usage(input_tokens=12, output_tokens=7)
    assert end.stop_reason == "tool_use"

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["json"] == {
        "model": "claude-test",
        "max_tokens": 2048,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]}
        ],
        "stream": True,
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "system": "be brief",
        "tools": [
            {
                "name": "echo",
                "description": "原样返回 text 参数",
                "input_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                },
            }
        ],
    }
    assert captured["headers"]["x-api-key"] == "config-key"


def test_stream_omits_optional_fields_and_ends_with_model_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_transport(
        monkeypatch, _sse(_message_start(), {"type": "message_stop"})
    )
    model = _model(thinking_budget_tokens=0)

    events = _collect(model)

    assert events == [
        ModelEnd(
            message=Message(role="assistant", content=[]),
            usage=Usage(),
            stop_reason="end_turn",
        )
    ]
    assert captured["json"] == {
        "model": "claude-test",
        "max_tokens": 2048,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]}
        ],
        "stream": True,
    }


def test_stream_translates_all_block_types_to_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_transport(
        monkeypatch, _sse(_message_start(), {"type": "message_stop"})
    )
    messages = [
        Message(
            role="assistant",
            content=[
                ThinkingBlock(thinking="t", signature="s"),
                RedactedThinkingBlock(data="opaque"),
                TextBlock("hi"),
                ToolUseBlock(id="c1", name="echo", input={"a": 1}),
            ],
        ),
        Message(
            role="user",
            content=[ToolResultBlock(tool_use_id="c1", content="ok", is_error=True)],
        ),
    ]

    _collect(_model(thinking_budget_tokens=0), messages=messages)

    assert captured["json"]["messages"] == [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "t", "signature": "s"},
                {"type": "redacted_thinking", "data": "opaque"},
                {"type": "text", "text": "hi"},
                {"type": "tool_use", "id": "c1", "name": "echo", "input": {"a": 1}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "c1",
                    "content": "ok",
                    "is_error": True,
                }
            ],
        },
    ]


def test_stream_assembles_redacted_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_transport(
        monkeypatch,
        _sse(
            _message_start(),
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "redacted_thinking", "data": "opaque"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 0},
            },
            {"type": "message_stop"},
        ),
    )

    events = _collect(_model())

    assert events[-1].message.content == [RedactedThinkingBlock(data="opaque")]


def test_stream_error_event_raises_model_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_transport(
        monkeypatch,
        b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"busy"}}\n\n',
    )
    with pytest.raises(ModelError, match="busy"):
        _collect(_model())


def test_stream_http_error_raises_model_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_transport(
        monkeypatch,
        json.dumps(
            {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
        ).encode(),
        status=400,
    )
    with pytest.raises(ModelError, match="bad"):
        _collect(_model())


def test_base_url_accepts_full_messages_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_transport(
        monkeypatch, _sse(_message_start(), {"type": "message_stop"})
    )

    _collect(_model(base_url="https://proxy.example.com/v1/messages"))

    assert captured["url"] == "https://proxy.example.com/v1/messages"


def test_model_identity_is_stripped() -> None:
    assert _model(model=" claude-test ").name == "claude-test"


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"api_key": ""}, "API key"),
        ({"base_url": ""}, "base URL"),
        ({"model": " "}, "model"),
    ],
)
def test_constructor_validates_params(overrides: dict, match: str) -> None:
    with pytest.raises(ModelError, match=match):
        _model(**overrides)
