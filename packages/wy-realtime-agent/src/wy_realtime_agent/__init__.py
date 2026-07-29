"""wy-realtime-agent:基于 wy-core 的实时语音 agent 应用。

由 Qwen-Audio 实时语音大模型驱动,经 WebSocket 全双工协议实现"麦克风流式
录音 → 服务端 VAD/语义轮次 → 流式语音播放"的实时对话。实时协议是服务端
维护上下文的推送式流,不走 ``wy_core.Model``/``Agent``/``Session``;复用
wy-core 的 ``Tool`` 契约(内置 read 工具 + MCP 工具)、``AuditLog`` 审计与
``ToolCall``/``ToolResult`` 事件词汇。``bootstrap`` 读 config.toml 的
[realtime] 与 [[mcp.servers]] 一站式组装;``create_agent`` 为可编程组装点。
控制台入口经 ``wy-realtime-agent`` 命令或 ``wy_realtime_agent.main:main``。
"""

from wy_realtime_agent.agent import (
    AssistantTranscript,
    Interrupted,
    RealtimeAgent,
    RealtimeEvent,
    SessionEnded,
    UserTranscript,
)
from wy_realtime_agent.audio import MicSource, SpeakerSink
from wy_realtime_agent.config import (
    ConfigError,
    MCPServerConfig,
    RealtimeConfig,
    load_mcp_config,
    load_realtime_config,
)
from wy_realtime_agent.factory import bootstrap, create_agent
from wy_realtime_agent.mcp import MCPClientManager
from wy_realtime_agent.protocol import (
    RealtimeClient,
    RealtimeError,
    build_session_config,
)
from wy_realtime_agent.tools import DEFAULT_TOOLS

__all__ = [
    "AssistantTranscript",
    "ConfigError",
    "DEFAULT_TOOLS",
    "Interrupted",
    "MCPClientManager",
    "MCPServerConfig",
    "MicSource",
    "RealtimeAgent",
    "RealtimeClient",
    "RealtimeConfig",
    "RealtimeError",
    "RealtimeEvent",
    "SessionEnded",
    "SpeakerSink",
    "UserTranscript",
    "bootstrap",
    "build_session_config",
    "create_agent",
    "load_mcp_config",
    "load_realtime_config",
]
