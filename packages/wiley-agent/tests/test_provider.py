import asyncio
from typing import Any

import pytest

from wiley_agent.provider import (
    BaseProvider,
    DoneEvent,
    TextDelta,
)


def test_provider_contract_is_abstract() -> None:
    with pytest.raises(TypeError):
        BaseProvider()  # type: ignore[abstract]


class _MinimalProvider(BaseProvider):
    """Smallest possible contract implementation."""

    async def stream_request(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ):
        yield TextDelta("ok", index=0)
        yield DoneEvent()


def test_minimal_implementation_satisfies_contract() -> None:
    provider = _MinimalProvider()
    assert provider.model == ""

    async def collect_events():
        return [
            event
            async for event in provider.stream_request(
                [{"role": "user", "content": "hi"}]
            )
        ]

    assert asyncio.run(collect_events()) == [TextDelta("ok", index=0), DoneEvent()]
