"""Module entry point that assembles a ready-to-use agent."""

from collections.abc import Sequence

from wiley_harness_agent.agent.config import load_config
from wiley_harness_agent.agent.conversation import ConversationService
from wiley_harness_agent.agent.service import AgentService
from wiley_harness_agent.agent.session import SessionStore
from wiley_harness_agent.agent.tools import DEFAULT_TOOLS, Tool


def create_agent(
    session_id: str | None = None,
    *,
    instruction: str | None = None,
    tools: Sequence[Tool] | None = None,
) -> ConversationService:
    """Create an agent backed by a durable session.

    传入 session_id 时恢复既有会话；省略时自动生成 UUID 新会话。
    tools 省略时启用内置默认工具集。
    """
    config = load_config()
    session = SessionStore(session_id)
    service = AgentService(
        config,
        instruction=instruction,
        tools=DEFAULT_TOOLS if tools is None else tuple(tools),
        messages=session.conversation_messages(),
        total_usage=session.total_usage,
    )
    return ConversationService(service, session)
