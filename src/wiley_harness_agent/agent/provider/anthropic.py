"""Anthropic Messages API over asynchronous HTTP, without the Anthropic SDK."""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

import aiohttp

from wiley_harness_agent.agent.provider.base import BaseProvider, ProviderError
from wiley_harness_agent.agent.provider.events import (
    DoneEvent,
    ErrorEvent,
    ProviderEvent,
    ProviderUsage,
    RedactedReasoning,
    ReasoningDelta,
    TextDelta,
    ThinkingSignature,
    ToolCall,
    ToolResult,
    UsageEvent,
)


class AnthropicProvider(BaseProvider):
    """aiohttp implementation of Anthropic's Messages API."""

    _timeout = aiohttp.ClientTimeout(
        total=None,
        connect=30,
        sock_connect=30,
        sock_read=300,
    )

    async def stream_request(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int,
        cache_control: dict[str, Any] | None = None,
        container: str | None = None,
        inference_geo: str | None = None,
        metadata: dict[str, Any] | None = None,
        output_config: dict[str, Any] | None = None,
        service_tier: str | None = None,
        stop_sequences: list[str] | None = None,
        system: str | list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        thinking: dict[str, Any] | None = None,
        tool_choice: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        top_k: int | None = None,
        top_p: float | None = None,
    ) -> AsyncIterator[ProviderEvent]:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
            "stream": True,
        }
        optional_fields = {
            "cache_control": cache_control,
            "container": container,
            "inference_geo": inference_geo,
            "metadata": metadata,
            "output_config": output_config,
            "service_tier": service_tier,
            "stop_sequences": stop_sequences,
            "system": system,
            "temperature": temperature,
            "thinking": thinking,
            "tool_choice": tool_choice,
            "tools": tools,
            "top_k": top_k,
            "top_p": top_p,
        }
        body.update(
            {name: value for name, value in optional_fields.items() if value is not None}
        )

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(
                    self._messages_url,
                    json=body,
                    headers=self._headers(),
                ) as response:
                    await self._raise_for_status(response)
                    event_data: list[str] = []
                    async for raw_line in response.content:
                        line = raw_line.decode("utf-8").rstrip("\r\n")
                        if line.startswith("data:"):
                            event_data.append(line[5:].lstrip())
                        elif not line and event_data:
                            parsed = from_event(self._decode_event("\n".join(event_data)))
                            if parsed is not None:
                                yield parsed
                            event_data = []
                    if event_data:
                        parsed = from_event(self._decode_event("\n".join(event_data)))
                        if parsed is not None:
                            yield parsed
        except ProviderError:
            raise
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ProviderError(f"Anthropic stream failed: {exc}") from exc

    @property
    def _messages_url(self) -> str:
        return self._base_url if self._base_url.endswith("/messages") else f"{self._base_url}/v1/messages"

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
        raise ProviderError(
            f"Anthropic request failed ({response.status}): {detail}"
        )

    @staticmethod
    def _decode_event(data: str) -> Mapping[str, Any]:
        if data == "[DONE]":
            return {"type": "done"}
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ProviderError("Anthropic SSE event must be a JSON object.")
        return payload


def from_event(event: Mapping[str, Any]) -> ProviderEvent | None:
    """Translate one Anthropic event payload into a provider-neutral event."""
    event_type = event.get("type")
    if event_type == "message_start":
        usage = event.get("message", {}).get("usage", {})
        return UsageEvent(_usage_from_mapping(usage))
    if event_type == "message_delta":
        usage = event.get("usage", {})
        stop_reason = event.get("delta", {}).get("stop_reason")
        if usage:
            return UsageEvent(
                _usage_from_mapping(usage), stop_reason=stop_reason
            )
        return DoneEvent(stop_reason=stop_reason)
    if event_type == "content_block_delta":
        index = int(event.get("index", 0))
        delta = event.get("delta", {})
        delta_type = delta.get("type")
        if delta_type == "text_delta":
            return TextDelta(str(delta.get("text", "")), index=index)
        if delta_type == "thinking_delta":
            return ReasoningDelta(str(delta.get("thinking", "")), index=index)
        if delta_type == "input_json_delta":
            return ToolCall(index=index, input_json=str(delta.get("partial_json", "")))
        if delta_type == "signature_delta":
            return ThinkingSignature(
                signature=str(delta.get("signature", "")), index=index
            )
    if event_type == "content_block_start":
        block = event.get("content_block", {})
        block_type = block.get("type")
        index = int(event.get("index", 0))
        if block_type == "thinking":
            return ReasoningDelta(str(block.get("thinking", "")), index=index)
        if block_type == "redacted_thinking":
            return RedactedReasoning(str(block.get("data", "")), index=index)
        if block_type == "text":
            return TextDelta(str(block.get("text", "")), index=index)
        if block.get("type") == "tool_use":
            return ToolCall(
                index=index,
                tool_call_id=str(block.get("id", "")),
                name=str(block.get("name", "")),
                input_json=json.dumps(block.get("input", {}) or {}),
                caller=block.get("caller"),
            )
        if block.get("type") == "tool_result":
            return ToolResult(
                tool_call_id=str(block.get("tool_use_id", "")),
                content=block.get("content", ""),
            )
    if event_type == "message_stop":
        return DoneEvent()
    if event_type == "error":
        error = event.get("error", {})
        return ErrorEvent(str(error.get("message", "Provider returned an error")), error.get("type"))
    if event_type == "done":
        return DoneEvent()
    # Lifecycle, ping, signature, and block-stop events carry no portable data.
    return None


def _usage_from_mapping(value: Mapping[str, Any]) -> ProviderUsage:
    return ProviderUsage(
        input_tokens=_token_count(value.get("input_tokens")),
        output_tokens=_token_count(value.get("output_tokens")),
        cache_creation_input_tokens=_token_count(
            value.get("cache_creation_input_tokens")
        ),
        cache_read_input_tokens=_token_count(value.get("cache_read_input_tokens")),
        cache_creation=value.get("cache_creation"),
        output_tokens_details=value.get("output_tokens_details"),
        server_tool_use=value.get("server_tool_use"),
        service_tier=_optional_string(value.get("service_tier")),
        inference_geo=_optional_string(value.get("inference_geo")),
    )


def _token_count(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None
