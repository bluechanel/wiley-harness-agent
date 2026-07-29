"""内置工具集。

本包只有 read 一个内置工具,显式列出即可,不需要 wy-coding-agent 那套
自动扫描;``mcp_tool`` 的 ``MCPTool`` 由 mcp 桥接层按连接构造,不进
``DEFAULT_TOOLS``。外部工具按 ``wy_core.Tool`` 契约编写后经
``create_agent(tools=...)`` 注入。
"""

from wy_core import Tool

from wy_realtime_agent.tools.read import READ

DEFAULT_TOOLS: tuple[Tool, ...] = (READ,)

__all__ = ["DEFAULT_TOOLS", "READ", "Tool"]
