"""All-in-one agent core: provider, tools, config, harness, sessions, MCP.

UI-agnostic. `bootstrap` reads config.toml and returns a ready-to-use agent
with the built-in `AnthropicProvider` and `DEFAULT_TOOLS`; `create_agent` is
the programmable assembly point for custom providers (`BaseProvider`) and
tools (`Tool`).
"""

from wiley_agent.config import (
    AnthropicConfig,
    ConfigError,
    DebugConfig,
    MCPServerConfig,
    load_config,
    load_debug_config,
    load_mcp_config,
)
from wiley_agent.conversation import ConversationService
from wiley_agent.debug import DebugRecorder
from wiley_agent.factory import bootstrap, create_agent
from wiley_agent.mcp import MCPClientManager
from wiley_agent.prompt_template import (
    AgentMDProvider,
    BasePromptProvider,
    MemoryProvider,
    ModelProvider,
    SkillProvider,
    WorkspaceProvider,
    build_prompt,
    default_prompt_providers,
)
from wiley_agent.provider import (
    AnthropicProvider,
    BaseProvider,
    DoneEvent,
    ErrorEvent,
    ProviderError,
    ProviderEvent,
    ProviderUsage,
    RedactedReasoning,
    ReasoningDelta,
    TextDelta,
    ThinkingSignature,
    ToolCall,
    ToolResult,
    UsageEvent,
)
from wiley_agent.service import AgentService, ChatResult, ChatStreamEvent
from wiley_agent.session import (
    SessionError,
    SessionRecord,
    SessionStore,
)
from wiley_agent.tools import DEFAULT_TOOLS, Tool
from wiley_agent.usage import ChatUsage

__all__ = [
    "AgentMDProvider",
    "AgentService",
    "AnthropicConfig",
    "AnthropicProvider",
    "BasePromptProvider",
    "BaseProvider",
    "ChatResult",
    "ChatStreamEvent",
    "ChatUsage",
    "ConfigError",
    "ConversationService",
    "DEFAULT_TOOLS",
    "DebugConfig",
    "DebugRecorder",
    "DoneEvent",
    "ErrorEvent",
    "MCPClientManager",
    "MCPServerConfig",
    "MemoryProvider",
    "ModelProvider",
    "ProviderError",
    "ProviderEvent",
    "ProviderUsage",
    "ReasoningDelta",
    "RedactedReasoning",
    "SessionError",
    "SessionRecord",
    "SessionStore",
    "SkillProvider",
    "TextDelta",
    "ThinkingSignature",
    "Tool",
    "ToolCall",
    "ToolResult",
    "UsageEvent",
    "WorkspaceProvider",
    "bootstrap",
    "build_prompt",
    "create_agent",
    "default_prompt_providers",
    "load_config",
    "load_debug_config",
    "load_mcp_config",
]
