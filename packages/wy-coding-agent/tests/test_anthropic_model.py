"""AnthropicModel:SSE 解码、请求体构造与完整消息组装(wy-core 契约)。"""

import asyncio

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
        "wy_coding_agent.anthropic.aiohttp.ClientSession",
        lambda **kwargs: Session(),
    )
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
        (
            b"event: message_start\n",
            b'data: {"type":"message_start","message":{"usage":{"input_tokens":12}}}\n',
            b"\n",
            b'data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}\n',
            b"\n",
            b'data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"why"}}\n',
            b"\n",
            b'data: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"sig"}}\n',
            b"\n",
            b'data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"hello"}}\n',
            b"\n",
            b'data: {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"call-1","name":"echo","input":{}}}\n',
            b"\n",
            b'data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\\"text\\":"}}\n',
            b"\n",
            b'data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"\\"hi\\"}"}}\n',
            b"\n",
            b'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":7}}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
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
    captured = _install_fake_transport(monkeypatch, (b"data: [DONE]\n", b"\n"))
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
    captured = _install_fake_transport(monkeypatch, (b"data: [DONE]\n", b"\n"))
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
        (
            b'data: {"type":"content_block_start","index":0,"content_block":{"type":"redacted_thinking","data":"opaque"}}\n',
            b"\n",
            b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{}}\n',
            b"\n",
        ),
    )

    events = _collect(_model())

    assert events[-1].message.content == [RedactedThinkingBlock(data="opaque")]


def test_stream_error_event_raises_model_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_transport(
        monkeypatch,
        (
            b'data: {"type":"error","error":{"type":"overloaded","message":"busy"}}\n',
            b"\n",
        ),
    )
    with pytest.raises(ModelError, match="busy"):
        _collect(_model())


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
