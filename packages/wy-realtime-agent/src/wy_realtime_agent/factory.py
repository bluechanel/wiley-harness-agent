"""Module entry point that assembles a ready-to-use realtime agent."""

from collections.abc import Sequence
from pathlib import Path

from wy_core import AuditLog, RealtimeAgent, Tool

from wy_realtime_agent.audio import MicSource, SpeakerSink
from wy_realtime_agent.config import (
    ConfigError,
    RealtimeConfig,
    load_mcp_config,
    load_realtime_config,
)
from wy_realtime_agent.mcp import MCPClientManager
from wy_realtime_agent.protocol import RealtimeClient
from wy_realtime_agent.qwen import QwenRealtimeModel
from wy_realtime_agent.tools import DEFAULT_TOOLS


def bootstrap(*, config_path: Path | None = None) -> RealtimeAgent:
    """一站式组装:读 config.toml 返回可直接运行的 RealtimeAgent。

    config_path 缺省为调用方 CWD 的 config.toml,显式传参可覆盖。同一份
    文件提供 [realtime](必填段)与 [[mcp.servers]](可选)。配置/连接
    失败抛 ConfigError,由调用方处理。需要自定义工具集或注入替身时,直接
    使用 `create_agent`。
    """
    config_path = config_path if config_path is not None else Path.cwd() / "config.toml"
    return create_agent(config=load_realtime_config(config_path), mcp_config=config_path)


def create_agent(
    *,
    config: RealtimeConfig,
    tools: Sequence[Tool] | None = None,
    client: RealtimeClient | None = None,
    mic: MicSource | None = None,
    speaker: SpeakerSink | None = None,
    mcp_config: Path | None = None,
    audit: bool = True,
) -> RealtimeAgent:
    """可编程组装点:组装 QwenRealtimeModel + wy_core.RealtimeAgent。

    tools 按 ``wy_core.Tool`` 契约编写后传入,省略时为内置 DEFAULT_TOOLS
    (read)。mcp_config 为 MCP 配置文件路径(TOML 的 [[mcp.servers]] 段),
    None 即不启用 MCP,传入时文件必须存在且合法;其中的 server 工具以
    mcp__<server>__<tool> 名称并入工具集,连接失败的 server 记 warning 并
    跳过,工具名冲突抛 ConfigError。client/mic/speaker 可注入替身(测试或
    自定义传输/音频)。config 的 instructions/echo_suppression 分别映射为
    RealtimeAgent 的 system/echo_suppression。审计默认写入 CWD/.wy_audit/,
    audit=False 关闭。调用方结束后应调用返回值的 close() 释放 MCP 连接。
    """
    base_tools = tuple(DEFAULT_TOOLS) if tools is None else tuple(tools)
    mcp_servers = load_mcp_config(mcp_config) if mcp_config is not None else ()
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

    try:
        return RealtimeAgent(
            model=QwenRealtimeModel(config, client=client),
            tools=all_tools,
            system=config.instructions or None,
            mic=mic if mic is not None else MicSource(),
            speaker=speaker if speaker is not None else SpeakerSink(),
            echo_suppression=config.echo_suppression,
            audit=AuditLog.default() if audit else None,
            closer=mcp_manager.close if mcp_manager is not None else None,
        )
    except BaseException:
        if mcp_manager is not None:
            mcp_manager.close()
        raise
