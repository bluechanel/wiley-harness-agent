"""wy-coding-agent:基于 wy-core 的编码 agent 应用。

核心运行时契约(Agent/Model/Tool/Session/AuditLog、消息与事件类型)在
`wy_core`;本包在其上提供应用层能力:AnthropicModel 模型实现、内置工具
(bash/grep/read/edit/write)、MCP 桥接、config.toml 解析、持久会话与
Textual TUI。`bootstrap` 读 config.toml 一站式组装;`create_agent` 为
可编程组装点(自定义模型/工具/提示词)。TUI 经 `wy-coding-agent` 命令或
`wy_coding_agent.main:main` 启动。
"""

from wy_coding_agent.anthropic import AnthropicModel, RedactedThinkingBlock
from wy_coding_agent.config import (
    AnthropicConfig,
    BashConfig,
    CompactionConfig,
    ConfigError,
    MCPServerConfig,
    load_bash_config,
    load_compaction_config,
    load_config,
    load_mcp_config,
    load_skills_config,
)
from wy_coding_agent.conversation import ConversationService
from wy_coding_agent.factory import bootstrap, create_agent
from wy_coding_agent.mcp import MCPClientManager
from wy_coding_agent.prompt_template import (
    AgentMDProvider,
    BasePromptProvider,
    DeferredToolProvider,
    MemoryProvider,
    ModelProvider,
    SkillProvider,
    WorkspaceProvider,
    build_prompt,
    default_prompt_providers,
)
from wy_coding_agent.reminders import (
    PlanModeState,
    ReminderProvider,
)
from wy_coding_agent.session import (
    SessionError,
    SessionRecord,
    SessionStore,
)
from wy_coding_agent.skills import Skill, default_skills_dirs, discover_skills
from wy_coding_agent.tool_policy import ApprovalHandler, WorkspaceToolHook
from wy_coding_agent.tools import DEFAULT_TOOLS
from wy_coding_agent.tools.agent import AgentTool
from wy_coding_agent.tools.plan import ExitPlanModeTool
from wy_coding_agent.tools.tool_search import ToolSearchTool
from wy_coding_agent.skills import SkillTool

__all__ = [
    "AgentMDProvider",
    "AgentTool",
    "AnthropicConfig",
    "ApprovalHandler",
    "AnthropicModel",
    "BashConfig",
    "BasePromptProvider",
    "CompactionConfig",
    "ConfigError",
    "ConversationService",
    "DEFAULT_TOOLS",
    "DeferredToolProvider",
    "ExitPlanModeTool",
    "MCPClientManager",
    "MCPServerConfig",
    "MemoryProvider",
    "ModelProvider",
    "PlanModeState",
    "RedactedThinkingBlock",
    "ReminderProvider",
    "SessionError",
    "SessionRecord",
    "SessionStore",
    "Skill",
    "SkillProvider",
    "SkillTool",
    "ToolSearchTool",
    "WorkspaceProvider",
    "WorkspaceToolHook",
    "bootstrap",
    "build_prompt",
    "create_agent",
    "default_prompt_providers",
    "default_skills_dirs",
    "discover_skills",
    "load_bash_config",
    "load_compaction_config",
    "load_config",
    "load_mcp_config",
    "load_skills_config",
]
