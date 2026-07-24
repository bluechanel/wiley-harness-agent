"""Provider contract used by the agent service."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from wiley_harness_agent.agent.provider.events import ProviderEvent


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
        **params: Any,
    ) -> AsyncIterator[ProviderEvent]:
        """Run one streaming request and decode the vendor SSE stream.

        Implementations must override this with `messages` required and every
        vendor-supported request parameter declared explicitly as a
        keyword-only argument — no opaque option pass-through, so an unknown
        parameter fails with TypeError at the call site. Parameter values are
        still decided by the caller (`AgentService._request_options`);
        providers add transport details (endpoint, headers, stream flag) and
        translate vendor events into the provider-neutral types.
        """
