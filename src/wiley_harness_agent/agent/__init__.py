"""Agent harness: config, providers, agent loop, tools, and session persistence.

This package is UI-agnostic. Front-ends (e.g. the TUI) should depend only on
the interfaces exported here; `create_agent` is the module entry point.
"""

from wiley_harness_agent.agent.config import AnthropicConfig, ConfigError, load_config
from wiley_harness_agent.agent.conversation import ConversationService
from wiley_harness_agent.agent.factory import create_agent
from wiley_harness_agent.agent.prompt_template import build_system_prompt
from wiley_harness_agent.agent.service import AgentService, ChatResult, ChatStreamEvent
from wiley_harness_agent.agent.session import (
    SessionError,
    SessionRecord,
    SessionStore,
)
from wiley_harness_agent.agent.tools import DEFAULT_TOOLS, Tool
from wiley_harness_agent.agent.usage import ChatUsage

__all__ = [
    "AgentService",
    "AnthropicConfig",
    "ChatResult",
    "ChatStreamEvent",
    "ChatUsage",
    "ConfigError",
    "ConversationService",
    "DEFAULT_TOOLS",
    "SessionError",
    "SessionRecord",
    "SessionStore",
    "Tool",
    "build_system_prompt",
    "create_agent",
    "load_config",
]
