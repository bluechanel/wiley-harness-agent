"""MCP integration: bridge + tool executor for Model Context Protocol servers.

``MCPClientManager`` (bridge) manages connection lifecycle and tool discovery;
``MCPTool`` (executor) wraps each remote tool as a ``wy_core.Tool`` for the
agent loop.  Re-export both from this package so callers can import from
``wy_coding_agent.mcp``.
"""

from wy_coding_agent.mcp.client import MCPClientManager, TOOL_NAME_PREFIX, tool_full_name
from wy_coding_agent.mcp.tool import MCPTool

__all__ = ["MCPClientManager", "MCPTool", "TOOL_NAME_PREFIX", "tool_full_name"]
