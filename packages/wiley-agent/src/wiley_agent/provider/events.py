"""Provider-neutral events emitted by streaming model requests."""

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str
    index: int = 0
    kind: Literal["text_delta"] = "text_delta"


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    text: str
    index: int = 0
    kind: Literal["reasoning_delta"] = "reasoning_delta"


@dataclass(frozen=True, slots=True)
class ThinkingSignature:
    signature: str
    index: int = 0
    kind: Literal["thinking_signature"] = "thinking_signature"


@dataclass(frozen=True, slots=True)
class RedactedReasoning:
    data: str
    index: int = 0
    kind: Literal["redacted_reasoning"] = "redacted_reasoning"


@dataclass(frozen=True, slots=True)
class ToolCall:
    index: int
    tool_call_id: str = ""
    name: str = ""
    input_json: str = ""
    caller: Any = None
    kind: Literal["tool_call"] = "tool_call"


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    content: Any
    kind: Literal["tool_result"] = "tool_result"


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    message: str
    code: str | None = None
    kind: Literal["error"] = "error"


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation: Any = None
    output_tokens_details: Any = None
    server_tool_use: Any = None
    service_tier: str | None = None
    inference_geo: str | None = None

    def add(self, other: "ProviderUsage") -> "ProviderUsage":
        return ProviderUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens
                + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
            cache_creation=other.cache_creation or self.cache_creation,
            output_tokens_details=(
                other.output_tokens_details or self.output_tokens_details
            ),
            server_tool_use=other.server_tool_use or self.server_tool_use,
            service_tier=other.service_tier or self.service_tier,
            inference_geo=other.inference_geo or self.inference_geo,
        )


@dataclass(frozen=True, slots=True)
class UsageEvent:
    usage: ProviderUsage
    stop_reason: str | None = None
    kind: Literal["usage"] = "usage"


@dataclass(frozen=True, slots=True)
class DoneEvent:
    stop_reason: str | None = None
    kind: Literal["done"] = "done"


ProviderEvent = (
    TextDelta | ReasoningDelta | ThinkingSignature | RedactedReasoning
    | ToolCall | ToolResult
    | ErrorEvent | UsageEvent | DoneEvent
)
