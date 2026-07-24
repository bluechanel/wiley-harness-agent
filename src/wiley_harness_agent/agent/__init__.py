"""Agent harness: config, providers, agent loop, tools, and session persistence.

This package is UI-agnostic. Front-ends (e.g. the TUI) should depend only on
the interfaces exported here; `create_agent` is the module entry point.
"""

from wiley_harness_agent.agent.config import (
    AnthropicConfig,
    ConfigError,
    DebugConfig,
    load_config,
    load_debug_config,
)
from wiley_harness_agent.agent.conversation import ConversationService
from wiley_harness_agent.agent.debug import DebugRecorder
from wiley_harness_agent.agent.factory import create_agent
from wiley_harness_agent.agent.prompt_template import (
    AgentMDProvider,
    BasePromptProvider,
    MemoryProvider,
    ModelProvider,
    SkillProvider,
    WorkspaceProvider,
    build_prompt,
    default_prompt_providers,
)
from wiley_harness_agent.agent.service import AgentService, ChatResult, ChatStreamEvent
from wiley_harness_agent.agent.session import (
    SessionError,
    SessionRecord,
    SessionStore,
)
from wiley_harness_agent.agent.tools import DEFAULT_TOOLS, Tool
from wiley_harness_agent.agent.usage import ChatUsage

__all__ = [
    "AgentMDProvider",
    "AgentService",
    "AnthropicConfig",
    "BasePromptProvider",
    "ChatResult",
    "ChatStreamEvent",
    "ChatUsage",
    "ConfigError",
    "ConversationService",
    "DEFAULT_TOOLS",
    "DebugConfig",
    "DebugRecorder",
    "MemoryProvider",
    "ModelProvider",
    "SessionError",
    "SessionRecord",
    "SessionStore",
    "SkillProvider",
    "Tool",
    "WorkspaceProvider",
    "build_prompt",
    "create_agent",
    "default_prompt_providers",
    "load_config",
    "load_debug_config",
]
