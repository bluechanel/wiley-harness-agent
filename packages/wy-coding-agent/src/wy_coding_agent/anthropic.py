"""Anthropic Messages API 的 Model 实现(官方 anthropic SDK)。

厂商参数(api_key、base_url、model、max_tokens、thinking 预算)全部在
构造期注入。按 wy-core 契约:``stream`` 边收边产出 TextDelta/ThinkingDelta
增量,SSE 解析、内容块累积与重试由 SDK 的流式 helper 完成,流末把 SDK
的最终消息翻译为 wy-core assistant 消息,以一个 ``ModelEnd`` 交付;
传输/解码/流内厂商错误一律 raise ``ModelError``。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import Message as AnthropicMessage
from anthropic.types import Usage as AnthropicUsage

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

_MESSAGES_SUFFIX = "/v1/messages"


@dataclass
class RedactedThinkingBlock:
    """Anthropic 加密思考块;应用侧扩展块,随消息在 wy-core 中原样往返。"""

    data: str
    type: str = "redacted_thinking"


class AnthropicModel(Model):
    """Official anthropic SDK implementation of Anthropic's Messages API."""

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
        base = base_url.strip().rstrip("/")
        # 兼容旧配置:base_url 允许填完整 messages endpoint,SDK 只收根地址。
        if base.endswith(_MESSAGES_SUFFIX):
            base = base[: -len(_MESSAGES_SUFFIX)]
        self._base_url = base
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
        params: dict[str, Any] = {
            "model": self.name,
            "max_tokens": self._max_tokens,
            "messages": [_message_to_wire(message) for message in messages],
        }
        if self._thinking_budget_tokens:
            params["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget_tokens,
            }
        if system is not None:
            params["system"] = system
        if tools is not None:
            params["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in tools
            ]

        try:
            async with AsyncAnthropic(
                api_key=self._api_key, base_url=self._base_url
            ) as client:
                async with client.messages.stream(**params) as sdk_stream:
                    async for event in sdk_stream:
                        if event.type == "content_block_start":
                            block = event.content_block
                            if block.type == "text" and block.text:
                                yield TextDelta(block.text)
                            elif block.type == "thinking" and block.thinking:
                                yield ThinkingDelta(block.thinking)
                        elif event.type == "content_block_delta":
                            delta = event.delta
                            if delta.type == "text_delta" and delta.text:
                                yield TextDelta(delta.text)
                            elif delta.type == "thinking_delta" and delta.thinking:
                                yield ThinkingDelta(delta.thinking)
                    final = await sdk_stream.get_final_message()
        except ModelError:
            raise
        except Exception as exc:
            # SDK 管线的失败形态众多(AnthropicError 族、httpx 传输错误、
            # 解码失败、残缺流的累积器断言),契约要求一律收敛为 ModelError。
            raise ModelError(f"Anthropic stream failed: {exc}") from exc

        yield ModelEnd(
            message=_from_sdk_message(final),
            usage=_usage_from(final.usage),
            stop_reason=final.stop_reason or "end_turn",
        )


def _from_sdk_message(message: AnthropicMessage) -> Message:
    """SDK 最终消息 → wy-core assistant 消息(空文本块跳过,未知块忽略)。"""
    content: list[Any] = []
    for block in message.content:
        if block.type == "text":
            if block.text:
                content.append(TextBlock(block.text))
        elif block.type == "thinking":
            content.append(
                ThinkingBlock(thinking=block.thinking, signature=block.signature or "")
            )
        elif block.type == "redacted_thinking":
            content.append(RedactedThinkingBlock(data=block.data))
        elif block.type == "tool_use":
            input_value = block.input if isinstance(block.input, dict) else {}
            content.append(
                ToolUseBlock(id=block.id, name=block.name, input=dict(input_value))
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


def _usage_from(usage: AnthropicUsage) -> Usage:
    return Usage(
        input_tokens=usage.input_tokens or 0,
        output_tokens=usage.output_tokens or 0,
        cache_write_tokens=usage.cache_creation_input_tokens or 0,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
    )
