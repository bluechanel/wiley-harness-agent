"""Provider contract used by the chat service."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from typing import Any

from wiley_harness_agent.provider.events import ProviderEvent


class ProviderError(RuntimeError):
    """Raised when a provider request cannot be completed or decoded."""


class BaseProvider(ABC):
    """Minimal asynchronous provider contract."""

    def __init__(self, *, api_key: str, base_url: str) -> None:
        self._api_key = api_key.strip()
        self._base_url = base_url.strip().rstrip("/")
        if not self._api_key:
            raise ProviderError(f"{type(self).__name__} API key is empty.")
        if not self._base_url:
            raise ProviderError(f"{type(self).__name__} base URL is empty.")

    def get_base_url(self) -> str:
        """Return the provider API base URL."""
        return self._base_url

    def get_api_key(self) -> str:
        """Return the API key used for authentication."""
        return self._api_key

    @abstractmethod
    def stream_request(
        self,
        messages: list[dict[str, Any]],
        *,
        model_name: str | None = None,
        reasoning: Mapping[str, Any] | None = None,
        **options: Any,
    ) -> AsyncIterator[ProviderEvent]:
        """Stream a model response for the supplied conversation messages."""
