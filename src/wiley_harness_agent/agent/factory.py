"""Module entry point that assembles a ready-to-use agent."""

from collections.abc import Sequence

from wiley_harness_agent.agent.config import load_config, load_debug_config
from wiley_harness_agent.agent.conversation import ConversationService
from wiley_harness_agent.agent.debug import DebugRecorder
from wiley_harness_agent.agent.prompt_template import (
    BasePromptProvider,
    default_prompt_providers,
)
from wiley_harness_agent.agent.service import AgentService
from wiley_harness_agent.agent.session import SessionStore
from wiley_harness_agent.agent.tools import DEFAULT_TOOLS, Tool


def create_agent(
    session_id: str | None = None,
    *,
    instruction: str | None = None,
    tools: Sequence[Tool] | None = None,
    prompt_providers: Sequence[BasePromptProvider] | None = None,
) -> ConversationService:
    """Create an agent backed by a durable session.

    传入 session_id 时恢复既有会话；省略时自动生成 UUID 新会话。
    tools 省略时启用内置默认工具集。
    prompt_providers 省略时使用 default_prompt_providers 的默认组合。
    config.toml 配置 [debug] enabled = true 时，把执行轨迹记录到
    sessions/<session_id>.debug.jsonl。
    """
    config = load_config()
    session = SessionStore(session_id)
    debug_recorder: DebugRecorder | None = None
    if load_debug_config().enabled:
        debug_recorder = DebugRecorder(session.path.with_suffix(".debug.jsonl"))
        debug_recorder.record_session_start(
            session_id=session.session_id,
            model=config.model,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            thinking_budget_tokens=config.thinking_budget_tokens,
        )
    service = AgentService(
        config,
        instruction=instruction,
        tools=DEFAULT_TOOLS if tools is None else tuple(tools),
        prompt_providers=(
            default_prompt_providers(config)
            if prompt_providers is None
            else tuple(prompt_providers)
        ),
        messages=session.conversation_messages(),
        total_usage=session.total_usage,
        debug_recorder=debug_recorder,
    )
    return ConversationService(service, session)
