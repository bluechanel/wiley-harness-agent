import asyncio
import json
from dataclasses import dataclass
from typing import AsyncIterator, Literal

from wiley_harness_agent.config import AnthropicConfig
from wiley_harness_agent.provider import (
    AnthropicProvider,
    DoneEvent,
    ErrorEvent,
    ProviderError,
    ProviderUsage,
    RedactedReasoning,
    ReasoningDelta,
    TextDelta,
    ThinkingSignature,
    ToolCall,
    UsageEvent,
)
from wiley_harness_agent.usage import ChatUsage
from wiley_harness_agent.text_editor import TEXT_EDITOR_TOOL, TextEditorError, execute_text_editor


@dataclass(frozen=True, slots=True)
class ChatResult:
    answer: str
    reasoning: str | None = None
    usage: "ChatUsage | None" = None


@dataclass(frozen=True, slots=True)
class ChatStreamEvent:
    kind: Literal["reasoning", "answer", "usage", "done"]
    text: str = ""
    usage: ChatUsage | None = None
    total_usage: ChatUsage | None = None


class ChatService:
    """Manage Anthropic requests and in-memory conversation history."""

    def __init__(
        self,
        config: AnthropicConfig,
        *,
        messages: list[dict[str, str]] | None = None,
        total_usage: ChatUsage | None = None,
    ) -> None:
        self._provider = AnthropicProvider(api_key=config.api_key, base_url=config.base_url)
        self._model = config.model
        self._max_tokens = config.max_tokens
        self._thinking_budget_tokens = config.thinking_budget_tokens
        self._messages = list(messages or [])
        self._total_usage = total_usage or ChatUsage()
        self._stream_lock = asyncio.Lock()

    async def send(self, user_input: str) -> ChatResult:
        answer_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: ChatUsage | None = None

        async for event in self.stream(user_input):
            if event.kind == "answer":
                answer_parts.append(event.text)
            elif event.kind == "reasoning":
                reasoning_parts.append(event.text)
            elif event.kind == "usage":
                usage = event.usage

        return ChatResult(
            answer="".join(answer_parts),
            reasoning="".join(reasoning_parts) or None,
            usage=usage,
        )

    async def stream(self, user_input: str) -> AsyncIterator[ChatStreamEvent]:
        """Yield thinking and answer text as it arrives from Anthropic."""
        async with self._stream_lock:
            initial_message_count = len(self._messages)
            self._messages.append({"role": "user", "content": user_input})
            answer_parts: list[str] = []
            try:
                reasoning = None
                if self._thinking_budget_tokens:
                    reasoning = {
                        "type": "enabled",
                        "budget_tokens": self._thinking_budget_tokens,
                    }

                accumulated_usage = ProviderUsage()
                while True:
                    response_stream = self._provider.stream_request(
                        self._messages,
                        model_name=self._model,
                        reasoning=reasoning,
                        max_tokens=self._max_tokens
                    )
                    usage = ProviderUsage()
                    tool_inputs: dict[int, str] = {}
                    tool_blocks: dict[int, dict[str, object]] = {}
                    thinking_blocks: dict[int, dict[str, object]] = {}
                    text_blocks: dict[int, dict[str, object]] = {}
                    stop_reason: str | None = None
                    async for event in response_stream:
                        if isinstance(event, UsageEvent):
                            usage = usage.add(event.usage)
                            stop_reason = event.stop_reason or stop_reason
                        elif isinstance(event, ReasoningDelta):
                            block = thinking_blocks.setdefault(
                                event.index, {"type": "thinking", "thinking": ""}
                            )
                            block["thinking"] = (
                                str(block["thinking"]) + event.text
                            )
                            if event.text:
                                yield ChatStreamEvent(kind="reasoning", text=event.text)
                        elif isinstance(event, ThinkingSignature):
                            thinking_blocks.setdefault(
                                event.index, {"type": "thinking", "thinking": ""}
                            )["signature"] = event.signature
                        elif isinstance(event, RedactedReasoning):
                            thinking_blocks[event.index] = {
                                "type": "redacted_thinking",
                                "data": event.data,
                            }
                        elif isinstance(event, TextDelta):
                            block = text_blocks.setdefault(
                                event.index, {"type": "text", "text": ""}
                            )
                            block["text"] = str(block["text"]) + event.text
                            if event.text:
                                answer_parts.append(event.text)
                                yield ChatStreamEvent(kind="answer", text=event.text)
                        elif isinstance(event, ToolCall):
                            block = tool_blocks.setdefault(
                                event.index,
                                {"type": "tool_use", "id": "", "name": ""},
                            )
                            if event.tool_call_id:
                                block["id"] = event.tool_call_id
                            if event.name:
                                block["name"] = event.name
                            if event.caller is not None:
                                block["caller"] = event.caller
                            if event.input_json and event.input_json != "{}":
                                tool_inputs[event.index] = (
                                    tool_inputs.get(event.index, "") + event.input_json
                                )
                        elif isinstance(event, ErrorEvent):
                            raise ProviderError(event.message)
                        elif isinstance(event, DoneEvent):
                            stop_reason = event.stop_reason or stop_reason
                    blocks = {
                        **thinking_blocks,
                        **text_blocks,
                        **tool_blocks,
                    }
                    final_content = [blocks[index] for index in sorted(blocks)]
                    accumulated_usage = accumulated_usage.add(usage)
                    if stop_reason != "tool_use":
                        break
                    self._messages.append({"role": "assistant", "content": final_content})
                    results = []
                    for index in sorted(tool_blocks):
                        block = tool_blocks[index]
                        arguments = json.loads(tool_inputs.get(index, "{}"))
                        block["input"] = arguments
                        try:
                            result = execute_text_editor(arguments)
                        except (TextEditorError, OSError) as exc:
                            result = f"Error: {exc}"
                        results.append({"type": "tool_result", "tool_use_id": block.get("id", ""), "content": result})
                    self._messages.append({"role": "user", "content": results})
                chat_usage = _to_chat_usage(accumulated_usage)
                self._total_usage = self._total_usage.add(chat_usage)
                yield ChatStreamEvent(
                    kind="usage",
                    usage=chat_usage,
                    total_usage=self._total_usage,
                )
                self._messages.append(
                    {"role": "assistant", "content": "".join(answer_parts)}
                )
                yield ChatStreamEvent(kind="done")
            except BaseException:
                del self._messages[initial_message_count:]
                raise


def _to_chat_usage(usage: ProviderUsage) -> ChatUsage:
    return ChatUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
    )
