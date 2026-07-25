"""Module entry point that assembles a ready-to-use agent."""

from collections.abc import Sequence
from pathlib import Path

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
from wiley_agent.mcp import MCPClientManager
from wiley_agent.prompt_template import (
    BasePromptProvider,
    default_prompt_providers,
)
from wiley_agent.service import AgentService
from wiley_agent.session import SessionStore
from wiley_agent.tools import DEFAULT_TOOLS, Tool


def create_agent(
    session_id: str | None = None,
    *,
    config: AnthropicConfig | None = None,
    config_path: Path | None = None,
    instruction: str | None = None,
    tools: Sequence[Tool] | None = None,
    prompt_providers: Sequence[BasePromptProvider] | None = None,
    workspace: Path | None = None,
    sessions_dir: Path | None = None,
    debug_config: DebugConfig | None = None,
    mcp_servers: Sequence[MCPServerConfig] | None = None,
) -> ConversationService:
    """Create an agent backed by a durable session.

    传入 session_id 时恢复既有会话；省略时自动生成 UUID 新会话。
    配置注入：显式对象优先，config/debug_config/mcp_servers 为 None 的项
    从 config_path（默认当前目录 config.toml）加载；宿主项目可全部显式
    传入而不依赖 config.toml。传 mcp_servers=() 可在配置了 server 的情况
    下禁用 MCP，debug_config=DebugConfig() 同理。
    tools 省略时启用内置默认工具集；prompt_providers 省略时使用
    default_prompt_providers(config, workspace) 的默认组合；sessions_dir
    省略时写入当前目录 sessions/。debug 开启时把执行轨迹记录到会话文件
    同目录的 <session_id>.debug.jsonl。
    配置 MCP server 时连接各 server 并把其工具以 mcp__<server>__<tool>
    名称并入工具集；连接失败的 server 记 warning 并跳过。调用方结束后应
    调用返回值的 close() 释放连接。
    """
    if config is None:
        config = load_config(config_path)
    if debug_config is None:
        debug_config = load_debug_config(config_path)
    if mcp_servers is None:
        mcp_servers = load_mcp_config(config_path)

    session = SessionStore(session_id, sessions_dir=sessions_dir)
    debug_recorder: DebugRecorder | None = None
    if debug_config.enabled:
        debug_recorder = DebugRecorder(session.path.with_suffix(".debug.jsonl"))
        debug_recorder.record_session_start(
            session_id=session.session_id,
            model=config.model,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            thinking_budget_tokens=config.thinking_budget_tokens,
        )
    base_tools = DEFAULT_TOOLS if tools is None else tuple(tools)
    mcp_manager: MCPClientManager | None = None
    if mcp_servers:
        mcp_manager = MCPClientManager(tuple(mcp_servers))
        mcp_manager.start()
    all_tools = base_tools + (mcp_manager.tools if mcp_manager else ())
    names = [tool.name for tool in all_tools]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        if mcp_manager is not None:
            mcp_manager.close()
        raise ConfigError(f"工具名冲突：{sorted(duplicates)}")
    service = AgentService(
        config,
        instruction=instruction,
        tools=all_tools,
        prompt_providers=(
            default_prompt_providers(config, workspace)
            if prompt_providers is None
            else tuple(prompt_providers)
        ),
        messages=session.conversation_messages(),
        total_usage=session.total_usage,
        debug_recorder=debug_recorder,
    )
    return ConversationService(
        service,
        session,
        closer=mcp_manager.close if mcp_manager is not None else None,
    )
