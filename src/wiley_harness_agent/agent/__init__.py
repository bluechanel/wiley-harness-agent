"""Agent harness: config, providers, chat loop, tools, and session persistence.

This package is UI-agnostic. Front-ends (e.g. the TUI) should depend only on
the interfaces exported here.
"""

from wiley_harness_agent.agent.chat import ChatResult, ChatService, ChatStreamEvent
from wiley_harness_agent.agent.config import AnthropicConfig, ConfigError, load_config
from wiley_harness_agent.agent.conversation import ConversationService
from wiley_harness_agent.agent.session import (
    SessionError,
    SessionRecord,
    SessionStore,
)
from wiley_harness_agent.agent.usage import ChatUsage

__all__ = [
    "AnthropicConfig",
    "ChatResult",
    "ChatService",
    "ChatStreamEvent",
    "ChatUsage",
    "ConfigError",
    "ConversationService",
    "SessionError",
    "SessionRecord",
    "SessionStore",
    "load_config",
]
