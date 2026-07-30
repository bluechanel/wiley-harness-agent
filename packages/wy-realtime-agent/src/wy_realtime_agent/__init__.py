"""wy-realtime-agent:wy-core realtime 契约的 Qwen-Audio 具体实现。

实时编排(打断、回声抑制、收集式 function calling、send_user_text 后台
指令注入)在 ``wy_core.RealtimeAgent``;本包提供它的 Qwen 实现件:
``QwenRealtimeModel``(WebSocket wire 协议 ↔ 类型化事件翻译,协议参考
包内 realtime_llm_ws.md)、``MicSource``/``SpeakerSink``(wy_core 音频
契约的 sounddevice 实现,16k 入 / 24k 出)、``[realtime]`` 配置解析与
MCP 工具桥接(read 内置工具 + MCPTool)。``bootstrap`` 读 config.toml
的 [realtime] 与 [[mcp.servers]] 一站式组装;``create_agent`` 为可编程
组装点。控制台入口经 ``wy-realtime-agent`` 命令或
``wy_realtime_agent.main:main``。编排事件与契约类型(RealtimeAgent、
RealtimeEvent、RealtimeError 等)从 wy_core re-export 以保持兼容。
"""

from wy_core import (
    AssistantTranscript,
    Interrupted,
    RealtimeAgent,
    RealtimeError,
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
from wy_realtime_agent.protocol import RealtimeClient, build_session_config
from wy_realtime_agent.qwen import QwenRealtimeModel
from wy_realtime_agent.tools import DEFAULT_TOOLS

__all__ = [
    "AssistantTranscript",
    "ConfigError",
    "DEFAULT_TOOLS",
    "Interrupted",
    "MCPClientManager",
    "MCPServerConfig",
    "MicSource",
    "QwenRealtimeModel",
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
