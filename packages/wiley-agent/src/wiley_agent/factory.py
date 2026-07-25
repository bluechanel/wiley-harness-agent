"""Module entry point that assembles a ready-to-use agent."""

from collections.abc import Sequence
from pathlib import Path

from wiley_agent.config import (
    ConfigError,
    DebugConfig,
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
from wiley_agent.provider import BaseProvider
from wiley_agent.provider.anthropic import AnthropicProvider
from wiley_agent.service import AgentService
from wiley_agent.session import SessionStore
from wiley_agent.tools import DEFAULT_TOOLS, Tool


def bootstrap(
    session_id: str | None = None,
    *,
    config_path: Path | None = None,
) -> ConversationService:
    """One-stop assembly: read config.toml and return a ready-to-use agent.

    config_path 缺省为调用方 CWD 的 config.toml，显式传参可覆盖。同一份文件
    提供 [anthropic]（provider 构造参数，必填段）、[debug]（可选）与
    [[mcp.servers]]（可选）配置。组装内容：`load_config` 构造
    `AnthropicProvider`、内置工具集 `DEFAULT_TOOLS`、`load_debug_config` 的
    debug 开关，最终交给 `create_agent`（mcp_config 即同一配置文件）。
    session_id 语义同 `create_agent`。配置/连接失败抛
    ConfigError/ProviderError/SessionError，由调用方处理。
    需要自定义 provider、工具集或提示词组合时，直接使用 `create_agent`。
    """
    config_path = config_path if config_path is not None else Path.cwd() / "config.toml"
    config = load_config(config_path)
    provider = AnthropicProvider(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        max_tokens=config.max_tokens,
        thinking_budget_tokens=config.thinking_budget_tokens,
    )
    return create_agent(
        session_id,
        provider=provider,
        tools=DEFAULT_TOOLS,
        debug_config=load_debug_config(config_path),
        mcp_config=config_path,
    )


def create_agent(
    session_id: str | None = None,
    *,
    provider: BaseProvider,
    instruction: str | None = None,
    tools: Sequence[Tool] | None = None,
    prompt_providers: Sequence[BasePromptProvider] | None = None,
    workspace: Path | None = None,
    sessions_dir: Path | None = None,
    debug_config: DebugConfig | None = None,
    mcp_config: Path | None = None,
) -> ConversationService:
    """Create an agent backed by a durable session.

    传入 session_id 时恢复既有会话；省略时自动生成 UUID 新会话。
    provider 必填：宿主按 `BaseProvider` 契约实现模型请求方法后注入，
    厂商参数（模型名、鉴权、max_tokens 等）在 provider 构造期给定。
    配置注入：库不读取任何隐式配置文件。debug_config 由宿主解析后显式
    传入（None 即关闭）；mcp_config 为 MCP 配置文件路径（TOML 的
    [[mcp.servers]] 段，可与宿主的 config.toml 共用一个文件），None 即
    不启用 MCP，传入时文件必须存在且合法。
    tools 与 provider 同为宿主实现的契约：按 `Tool` 契约编写后传入，
    省略时无本地工具（仅配置的 MCP 工具可用）；prompt_providers 省略时使用
    default_prompt_providers(provider.model, workspace) 的默认组合；
    sessions_dir 省略时写入当前目录 .agent_session/。debug 开启时把执行轨迹
    记录到会话文件同目录的 <session_id>.debug.jsonl。
    配置 MCP 时连接文件里的各 server 并把其工具以 mcp__<server>__<tool>
    名称自动转为 Tool 并入工具集；连接失败的 server 记 warning 并跳过。
    调用方结束后应调用返回值的 close() 释放连接。
    """
    if debug_config is None:
        debug_config = DebugConfig()
    mcp_servers = load_mcp_config(mcp_config) if mcp_config is not None else ()

    session = SessionStore(session_id, sessions_dir=sessions_dir)
    debug_recorder: DebugRecorder | None = None
    if debug_config.enabled:
        debug_recorder = DebugRecorder(session.path.with_suffix(".debug.jsonl"))
        debug_recorder.record_session_start(
            session_id=session.session_id,
            provider=type(provider).__name__,
            model=provider.model,
        )
    base_tools = () if tools is None else tuple(tools)
    mcp_manager: MCPClientManager | None = None
    if mcp_servers:
        mcp_manager = MCPClientManager(mcp_servers)
        mcp_manager.start()
    all_tools = base_tools + (mcp_manager.tools if mcp_manager else ())
    names = [tool.name for tool in all_tools]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        if mcp_manager is not None:
            mcp_manager.close()
        raise ConfigError(f"工具名冲突：{sorted(duplicates)}")
    service = AgentService(
        provider,
        instruction=instruction,
        tools=all_tools,
        prompt_providers=(
            default_prompt_providers(provider.model, workspace)
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
