from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ChatUsage:
    """Token usage for one request or an accumulated conversation."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def cache_tokens(self) -> int:
        return self.cache_creation_input_tokens + self.cache_read_input_tokens

    @property
    def context_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def add(self, other: "ChatUsage") -> "ChatUsage":
        return ChatUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens
                + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ChatUsage":
        if not value:
            return cls()
        return cls(
            input_tokens=_as_token_count(value.get("input_tokens")),
            output_tokens=_as_token_count(value.get("output_tokens")),
            cache_creation_input_tokens=_as_token_count(
                value.get("cache_creation_input_tokens")
            ),
            cache_read_input_tokens=_as_token_count(
                value.get("cache_read_input_tokens")
            ),
        )


def _as_token_count(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0

