import asyncio
from pathlib import Path

from wiley_agent.conversation import ConversationService
from wiley_agent.service import ChatStreamEvent
from wiley_agent.session import SessionStore
from wiley_agent.usage import ChatUsage


class _Agent:
    """Fake AgentService streaming one tool round then a final answer."""

    async def stream(self, user_input):
        yield ChatStreamEvent(kind="reasoning", text="think")
        yield ChatStreamEvent(
            kind="tool_call",
            tool_name="bash",
            tool_call_id="call-1",
            tool_arguments={"command": "ls"},
        )
        yield ChatStreamEvent(
            kind="tool_output",
            text="Error: denied",
            tool_name="bash",
            tool_call_id="call-1",
            tool_is_error=True,
        )
        yield ChatStreamEvent(kind="answer", text="done")
        yield ChatStreamEvent(
            kind="usage",
            usage=ChatUsage(input_tokens=7, output_tokens=3),
            total_usage=ChatUsage(input_tokens=7, output_tokens=3),
            context_tokens=9,
        )
        yield ChatStreamEvent(kind="done")


def _drain(service: ConversationService) -> None:
    async def run():
        async for _ in service.stream("hi"):
            pass

    asyncio.run(run())


def test_stream_persists_tool_records_in_order(tmp_path: Path) -> None:
    store = SessionStore(sessions_dir=tmp_path)
    service = ConversationService(_Agent(), store)  # type: ignore[arg-type]

    _drain(service)

    assert [(record.role, record.kind) for record in store.records] == [
        ("user", "input"),
        ("tool_call", "tool_call"),
        ("tool_output", "tool_output"),
        ("assistant", "thinking"),
        ("assistant", "answer"),
    ]
    tool_call = store.records[1]
    assert tool_call.content == {"command": "ls"}
    assert tool_call.metadata == {"tool_name": "bash", "tool_call_id": "call-1"}
    tool_output = store.records[2]
    assert tool_output.content == "Error: denied"
    assert tool_output.metadata == {
        "tool_name": "bash",
        "tool_call_id": "call-1",
        "is_error": True,
    }


def test_stream_persists_context_tokens_and_restores(tmp_path: Path) -> None:
    store = SessionStore(sessions_dir=tmp_path)
    service = ConversationService(_Agent(), store)  # type: ignore[arg-type]

    _drain(service)

    answer = store.records[-1]
    assert (answer.metadata or {}).get("context_tokens") == 9
    assert service.total_usage == ChatUsage(input_tokens=7, output_tokens=3)
    assert service.last_context_tokens == 9

    restored = SessionStore(store.session_id, sessions_dir=tmp_path)
    assert restored.total_usage == ChatUsage(input_tokens=7, output_tokens=3)
    assert restored.last_context_tokens == 9
