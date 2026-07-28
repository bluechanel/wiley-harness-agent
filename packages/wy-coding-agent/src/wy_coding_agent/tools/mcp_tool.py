"""MCP 工具执行器:把远端 MCP 工具按 ``wy_core.Tool`` 契约暴露给 agent 循环。

本模块只负责"执行一次调用":同步 ``execute`` 经
``asyncio.run_coroutine_threadsafe`` 桥进 MCP 客户端的后台事件循环并等待
(120s 超时),再把 CallToolResult 翻译成 tool_result 文本(server 侧
isError → raise,由 wy-core 循环统一转 ``Error: ...``)。连接生命周期
(线程/事件循环/会话)归 ``wy_coding_agent.mcp`` 的桥接层:它实现
``acquire()`` 并在构造 ``MCPTool`` 时注入自己;本模块不 import MCP SDK。
"""

import asyncio
import json
from typing import Any, Protocol

from wy_core import Tool

_CALL_TIMEOUT_SECONDS = 120.0


class MCPChannel(Protocol):
    """执行一次调用所需的桥接侧最小接口。"""

    def acquire(self) -> tuple[Any, Any]:
        """Return (session, loop) for one call; raise RuntimeError when not connected."""
        ...


def result_to_text(result: Any) -> str:
    """Flatten a CallToolResult's content blocks into one tool_result string."""
    parts: list[str] = []
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
        else:
            # Non-text blocks (images, resources) fall back to their JSON dump.
            parts.append(block.model_dump_json(exclude_none=True))
    if parts:
        return "\n".join(parts)
    if result.structuredContent is not None:
        return json.dumps(result.structuredContent, ensure_ascii=False)
    return ""


class MCPTool(Tool):
    """One remote MCP tool, executing through the bridge's background loop."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters: dict,
        remote_name: str,
        channel: MCPChannel,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters
        self.remote_name = remote_name
        self._channel = channel

    def execute(self, input: dict) -> str:
        session, loop = self._channel.acquire()
        future = asyncio.run_coroutine_threadsafe(
            session.call_tool(self.remote_name, dict(input)), loop
        )
        try:
            result = future.result(_CALL_TIMEOUT_SECONDS)
        except TimeoutError:
            future.cancel()
            raise RuntimeError(
                f"MCP tool {self.remote_name!r} timed out after "
                f"{_CALL_TIMEOUT_SECONDS:.0f}s"
            ) from None
        text = result_to_text(result)
        if result.isError:
            raise RuntimeError(text or f"MCP tool {self.remote_name!r} failed")
        return text
