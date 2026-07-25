import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator, Literal

from wiley_agent.config import AnthropicConfig
from wiley_agent.debug import DebugRecorder
from wiley_agent.provider import (
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
from wiley_agent.prompt_template import BasePromptProvider, build_prompt
from wiley_agent.tools import Tool
from wiley_agent.usage import ChatUsage


@dataclass(frozen=True, slots=True)
class ChatResult:
    answer: str
    reasoning: str | None = None
    usage: "ChatUsage | None" = None


@dataclass(frozen=True, slots=True)
class ChatStreamEvent:
    kind: Literal["reasoning", "answer", "tool_call", "tool_output", "usage", "done"]
    text: str = ""
    usage: ChatUsage | None = None
    total_usage: ChatUsage | None = None
    context_tokens: int = 0
    tool_name: str = ""
    tool_call_id: str = ""
    tool_arguments: Mapping[str, Any] | None = None
    tool_is_error: bool = False


class AgentService:
    """Run the agent loop: model requests, tool execution, in-memory history."""

    def __init__(
        self,
        config: AnthropicConfig,
        *,
        instruction: str | None = None,
        tools: Sequence[Tool] = (),
        prompt_providers: Sequence[BasePromptProvider] = (),
        messages: list[dict[str, str]] | None = None,
        total_usage: ChatUsage | None = None,
        debug_recorder: DebugRecorder | None = None,
    ) -> None:
        self._provider = AnthropicProvider(api_key=config.api_key, base_url=config.base_url)
        self._model = config.model
        self._max_tokens = config.max_tokens
        self._thinking_budget_tokens = config.thinking_budget_tokens
        self._tools = tuple(tools)
        self._tools_by_name = {tool.name: tool for tool in self._tools}
        self._system_prompt = build_prompt(instruction, prompt_providers)
        self._messages = list(messages or [])
        self._total_usage = total_usage or ChatUsage()
        self._stream_lock = asyncio.Lock()
        self._debug = debug_recorder
        self._debug_turn = 0

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
        """Yield thinking and answer text as it arrives from the provider."""
        async with self._stream_lock:
            initial_message_count = len(self._messages)
            self._messages.append({"role": "user", "content": user_input})
            answer_parts: list[str] = []
            self._debug_turn += 1
            turn = self._debug_turn
            round_index = 0
            try:
                request_options = self._request_options()
                accumulated_usage = ProviderUsage()
                while True:
                    round_index += 1
                    if self._debug is not None:
                        self._debug.record_request(
                            turn=turn,
                            round_index=round_index,
                            body={
                                **request_options,
                                "messages": self._messages,
                                "stream": True,
                            },
                        )
                    response_stream = self._provider.stream_request(
                        self._messages,
                        **request_options,
                    )
                    usage = ProviderUsage()
                    tool_inputs: dict[int, str] = {}
                    tool_blocks: dict[int, dict[str, object]] = {}
                    thinking_blocks: dict[int, dict[str, object]] = {}
                    text_blocks: dict[int, dict[str, object]] = {}
                    stop_reason: str | None = None
                    async for event in response_stream:
                        if self._debug is not None:
                            self._debug.record_response_event(
                                turn=turn, round_index=round_index, event=event
                            )
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
                    if self._debug is not None:
                        self._debug.record_response_end(
                            turn=turn,
                            round_index=round_index,
                            stop_reason=stop_reason,
                            usage=asdict(usage),
                        )
                    if stop_reason != "tool_use":
                        break
                    self._messages.append({"role": "assistant", "content": final_content})
                    results = []
                    for index in sorted(tool_blocks):
                        block = tool_blocks[index]
                        arguments = json.loads(tool_inputs.get(index, "{}"))
                        block["input"] = arguments
                        tool_name = str(block.get("name", ""))
                        tool_call_id = str(block.get("id", ""))
                        yield ChatStreamEvent(
                            kind="tool_call",
                            tool_name=tool_name,
                            tool_call_id=tool_call_id,
                            tool_arguments=arguments,
                        )
                        if self._debug is not None:
                            self._debug.record_tool_call(
                                turn=turn,
                                round_index=round_index,
                                tool_name=tool_name,
                                tool_call_id=tool_call_id,
                                arguments=arguments,
                            )
                        result, is_error = await asyncio.to_thread(
                            self._execute_tool, tool_name, arguments
                        )
                        if self._debug is not None:
                            self._debug.record_tool_result(
                                turn=turn,
                                round_index=round_index,
                                tool_name=tool_name,
                                tool_call_id=tool_call_id,
                                output=result,
                            )
                        yield ChatStreamEvent(
                            kind="tool_output",
                            text=result,
                            tool_name=tool_name,
                            tool_call_id=tool_call_id,
                            tool_is_error=is_error,
                        )
                        tool_result: dict[str, object] = {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": result,
                        }
                        if is_error:
                            tool_result["is_error"] = True
                        results.append(tool_result)
                    self._messages.append({"role": "user", "content": results})
                chat_usage = _to_chat_usage(accumulated_usage)
                self._total_usage = self._total_usage.add(chat_usage)
                # `usage` holds the final round only; summing its components
                # gives the live context size, unlike the per-turn accumulation.
                yield ChatStreamEvent(
                    kind="usage",
                    usage=chat_usage,
                    total_usage=self._total_usage,
                    context_tokens=_to_chat_usage(usage).context_tokens,
                )
                self._messages.append(
                    {"role": "assistant", "content": "".join(answer_parts)}
                )
                yield ChatStreamEvent(kind="done")
            except BaseException as exc:
                if self._debug is not None:
                    self._debug.record_error(
                        turn=turn,
                        round_index=round_index,
                        error_type=type(exc).__name__,
                        message=str(exc),
                    )
                del self._messages[initial_message_count:]
                raise

    def _request_options(self) -> dict[str, Any]:
        """Assemble request parameter values for the provider's explicit params."""
        options: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
        }
        if self._thinking_budget_tokens:
            options["thinking"] = {
                "type": "enabled",
                "budget_tokens": self._thinking_budget_tokens,
            }
        if self._system_prompt:
            options["system"] = self._system_prompt
        if self._tools:
            options["tools"] = [dict(tool.definition) for tool in self._tools]
        return options

    def _execute_tool(
        self, name: str, arguments: Mapping[str, Any]
    ) -> tuple[str, bool]:
        """Run one registered tool; failures come back as an error tool result."""
        tool = self._tools_by_name.get(name)
        if tool is None:
            return f"Error: unknown tool: {name!r}", True
        try:
            return tool.execute(arguments), False
        except Exception as exc:
            return f"Error: {exc}", True


def _to_chat_usage(usage: ProviderUsage) -> ChatUsage:
    return ChatUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
    )
