"""Anthropic Messages API 的 Model 实现(aiohttp 直连,无 SDK)。

厂商参数(api_key、base_url、model、max_tokens、thinking 预算)全部在
构造期注入。按 wy-core 契约:``stream`` 边收边产出 TextDelta/ThinkingDelta
增量,流结束时在实现内把厂商 SSE 事件组装为完整 assistant 消息,以一个
``ModelEnd`` 交付;传输/解码/流内厂商错误一律 raise ``ModelError``。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp

from wy_core import (
    Message,
    Model,
    ModelEnd,
    ModelError,
    ModelEvent,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ThinkingDelta,
    Tool,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)


@dataclass
class RedactedThinkingBlock:
    """Anthropic 加密思考块;应用侧扩展块,随消息在 wy-core 中原样往返。"""

    data: str
    type: str = "redacted_thinking"


class AnthropicModel(Model):
    """aiohttp implementation of Anthropic's Messages API."""

    _timeout = aiohttp.ClientTimeout(
        total=None,
        connect=30,
        sock_connect=30,
        sock_read=300,
    )

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int = 8192,
        thinking_budget_tokens: int = 4096,
    ) -> None:
        self._api_key = api_key.strip()
        self._base_url = base_url.strip().rstrip("/")
        self._max_tokens = max_tokens
        self._thinking_budget_tokens = thinking_budget_tokens
        self.name = model.strip()
        if not self._api_key:
            raise ModelError(f"{type(self).__name__} API key is empty.")
        if not self._base_url:
            raise ModelError(f"{type(self).__name__} base URL is empty.")
        if not self.name:
            raise ModelError(f"{type(self).__name__} model is empty.")

    async def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: Sequence[Tool] | None = None,
    ) -> AsyncIterator[ModelEvent]:
        body: dict[str, Any] = {
            "model": self.name,
            "max_tokens": self._max_tokens,
            "messages": [_message_to_wire(message) for message in messages],
            "stream": True,
        }
        if self._thinking_budget_tokens:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget_tokens,
            }
        if system is not None:
            body["system"] = system
        if tools is not None:
            body["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in tools
            ]

        # 组装状态:按 index 累积 wire 形态的内容块与工具参数 JSON 片段。
        blocks: dict[int, dict[str, Any]] = {}
        json_parts: dict[int, list[str]] = {}
        usage = Usage()
        stop_reason: str | None = None

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as http:
                async with http.post(
                    self._messages_url,
                    json=body,
                    headers=self._headers(),
                ) as response:
                    await self._raise_for_status(response)
                    async for event in _sse_events(response):
                        event_type = event.get("type")
                        if event_type == "error":
                            error = event.get("error", {})
                            raise ModelError(
                                str(error.get("message", "Anthropic returned an error"))
                            )
                        if event_type == "message_start":
                            usage.add(
                                _usage_from(event.get("message", {}).get("usage", {}))
                            )
                        elif event_type == "message_delta":
                            usage.add(_usage_from(event.get("usage", {})))
                            stop_reason = (
                                event.get("delta", {}).get("stop_reason") or stop_reason
                            )
                        elif event_type == "content_block_start":
                            index = int(event.get("index", 0))
                            block = dict(event.get("content_block", {}))
                            blocks[index] = block
                            if block.get("type") == "text" and block.get("text"):
                                yield TextDelta(str(block["text"]))
                            if block.get("type") == "thinking" and block.get("thinking"):
                                yield ThinkingDelta(str(block["thinking"]))
                        elif event_type == "content_block_delta":
                            index = int(event.get("index", 0))
                            delta = event.get("delta", {})
                            delta_type = delta.get("type")
                            block = blocks.setdefault(index, {})
                            if delta_type == "text_delta":
                                text = str(delta.get("text", ""))
                                block.setdefault("type", "text")
                                block["text"] = str(block.get("text", "")) + text
                                if text:
                                    yield TextDelta(text)
                            elif delta_type == "thinking_delta":
                                thinking = str(delta.get("thinking", ""))
                                block.setdefault("type", "thinking")
                                block["thinking"] = (
                                    str(block.get("thinking", "")) + thinking
                                )
                                if thinking:
                                    yield ThinkingDelta(thinking)
                            elif delta_type == "input_json_delta":
                                json_parts.setdefault(index, []).append(
                                    str(delta.get("partial_json", ""))
                                )
                            elif delta_type == "signature_delta":
                                block["signature"] = str(block.get("signature", "")) + str(
                                    delta.get("signature", "")
                                )
        except ModelError:
            raise
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ModelError(f"Anthropic stream failed: {exc}") from exc

        yield ModelEnd(
            message=_assemble(blocks, json_parts),
            usage=usage,
            stop_reason=stop_reason or "end_turn",
        )

    @property
    def _messages_url(self) -> str:
        return (
            self._base_url
            if self._base_url.endswith("/messages")
            else f"{self._base_url}/v1/messages"
        )

    def _headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "accept": "text/event-stream",
        }

    @staticmethod
    async def _raise_for_status(response: aiohttp.ClientResponse) -> None:
        if response.status < 400:
            return
        detail = await response.text()
        raise ModelError(f"Anthropic request failed ({response.status}): {detail}")


async def _sse_events(
    response: aiohttp.ClientResponse,
) -> AsyncIterator[Mapping[str, Any]]:
    """Yield decoded SSE data payloads; [DONE] and empty keep-alives are skipped."""
    event_data: list[str] = []
    async for raw_line in response.content:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            event_data.append(line[5:].lstrip())
        elif not line and event_data:
            payload = _decode_event("\n".join(event_data))
            event_data = []
            if payload is not None:
                yield payload
    if event_data:
        payload = _decode_event("\n".join(event_data))
        if payload is not None:
            yield payload


def _decode_event(data: str) -> Mapping[str, Any] | None:
    if data == "[DONE]":
        return None
    payload = json.loads(data)
    if not isinstance(payload, dict):
        raise ModelError("Anthropic SSE event must be a JSON object.")
    return payload


def _assemble(
    blocks: Mapping[int, Mapping[str, Any]], json_parts: Mapping[int, list[str]]
) -> Message:
    """把累积的 wire 块组装为完整 assistant 消息(空文本块跳过)。"""
    content: list[Any] = []
    for index in sorted(blocks):
        block = blocks[index]
        block_type = block.get("type")
        if block_type == "text":
            text = str(block.get("text", ""))
            if text:
                content.append(TextBlock(text))
        elif block_type == "thinking":
            content.append(
                ThinkingBlock(
                    thinking=str(block.get("thinking", "")),
                    signature=str(block.get("signature", "")),
                )
            )
        elif block_type == "redacted_thinking":
            content.append(RedactedThinkingBlock(data=str(block.get("data", ""))))
        elif block_type == "tool_use":
            raw = "".join(json_parts.get(index, ()))
            try:
                input_value = json.loads(raw) if raw.strip() else dict(block.get("input") or {})
            except json.JSONDecodeError as exc:
                raise ModelError(f"工具入参 JSON 解析失败: {exc}") from exc
            content.append(
                ToolUseBlock(
                    id=str(block.get("id", "")),
                    name=str(block.get("name", "")),
                    input=input_value,
                )
            )
    return Message(role="assistant", content=content)


def _message_to_wire(message: Message) -> dict[str, Any]:
    """wy-core 消息 → Anthropic wire 格式(应用扩展块一并翻译)。"""
    content: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            content.append({"type": "text", "text": block.text})
        elif isinstance(block, ThinkingBlock):
            content.append(
                {
                    "type": "thinking",
                    "thinking": block.thinking,
                    "signature": block.signature,
                }
            )
        elif isinstance(block, RedactedThinkingBlock):
            content.append({"type": "redacted_thinking", "data": block.data})
        elif isinstance(block, ToolUseBlock):
            content.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                }
            )
        elif isinstance(block, ToolResultBlock):
            item: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": block.content,
            }
            if block.is_error:
                item["is_error"] = True
            content.append(item)
    return {"role": message.role, "content": content}


def _usage_from(value: Mapping[str, Any]) -> Usage:
    return Usage(
        input_tokens=_token_count(value.get("input_tokens")),
        output_tokens=_token_count(value.get("output_tokens")),
        cache_write_tokens=_token_count(value.get("cache_creation_input_tokens")),
        cache_read_tokens=_token_count(value.get("cache_read_input_tokens")),
    )


def _token_count(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
