"""MCP client: bridge configured MCP servers into the agent loop.

The MCP SDK is fully async and its transport/session objects are task-scoped
async context managers, while the harness ``Tool`` contract is synchronous.
``MCPClientManager`` therefore runs a dedicated background thread with its own
event loop; each server connection lives inside one long-lived task on that
loop. This module owns only the bridging — connection lifecycle, tool naming
and discovery; per-call execution lives in
``wy_coding_agent.mcp.tool.MCPTool``, which reaches the loop through
``_ServerConnection.acquire()``.

Server connect failures are logged and skipped — a broken MCP server must not
prevent the agent from starting. There is no sandbox: MCP servers run (stdio)
or are reached (http) with the current user's privileges.
"""

import asyncio
import logging
import threading
from collections.abc import Sequence
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from wy_core import Tool

from wy_coding_agent.config import MCPServerConfig
from wy_coding_agent.mcp.tool import MCPTool

_log = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 30.0
_CLOSE_TIMEOUT_SECONDS = 10.0

TOOL_NAME_PREFIX = "mcp"


def tool_full_name(server_name: str, tool_name: str) -> str:
    return f"{TOOL_NAME_PREFIX}__{server_name}__{tool_name}"


class _ServerConnection:
    """One server's lifecycle: connect, list tools, serve calls, stop on demand."""

    def __init__(
        self, config: MCPServerConfig, loop: asyncio.AbstractEventLoop
    ) -> None:
        self.config = config
        self.loop = loop
        self.session: ClientSession | None = None
        self.remote_tools: tuple[Any, ...] = ()
        self.closed = False  # set by the manager; gates acquire() immediately
        self.ready = threading.Event()  # set on success OR failure; check session
        self.done = threading.Event()  # set when run() has fully unwound
        self._stop = asyncio.Event()

    async def run(self) -> None:
        """Hold the transport/session contexts open until asked to stop.

        The whole ``async with`` stack is entered and exited inside this single
        task, as the SDK's anyio cancel scopes require.
        """
        try:
            if self.config.transport == "stdio":
                params = StdioServerParameters(
                    command=self.config.command,
                    args=list(self.config.args),
                    env={**get_default_environment(), **self.config.env},
                )
                async with stdio_client(params) as (read, write):
                    await self._serve(read, write)
            else:
                async with streamablehttp_client(
                    self.config.url, headers=dict(self.config.headers) or None
                ) as (read, write, _get_session_id):
                    await self._serve(read, write)
        except BaseException as exc:
            if not self.ready.is_set():
                _log.warning(
                    "MCP server %r connection failed: %s", self.config.name, exc
                )
            else:
                _log.warning("MCP server %r terminated: %s", self.config.name, exc)
        finally:
            self.session = None
            self.ready.set()
            self.done.set()

    async def _serve(self, read: Any, write: Any) -> None:
        async with ClientSession(read, write) as session:
            await session.initialize()
            self.remote_tools = tuple((await session.list_tools()).tools)
            self.session = session
            _log.info(
                "MCP server %r connected with %d tools",
                self.config.name,
                len(self.remote_tools),
            )
            self.ready.set()
            await self._stop.wait()

    def request_stop(self) -> None:
        """Ask run() to unwind; must be invoked on the manager's loop."""
        self._stop.set()

    def acquire(self) -> tuple[ClientSession, asyncio.AbstractEventLoop]:
        """MCPTool's per-call entry: current (session, loop), or raise when down."""
        session = self.session
        if self.closed or session is None:
            raise RuntimeError(f"MCP server {self.config.name!r} is not connected")
        return session, self.loop


class MCPClientManager:
    """Own the background loop thread and expose MCP tools as ``Tool`` instances."""

    def __init__(self, servers: Sequence[MCPServerConfig]) -> None:
        self._servers = tuple(servers)
        self._connections: tuple[_ServerConnection, ...] = ()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def start(self, timeout: float = _CONNECT_TIMEOUT_SECONDS) -> None:
        """Connect to all configured servers, blocking until ready or timeout."""
        if self._thread is not None:
            raise RuntimeError("MCPClientManager already started")
        if not self._servers:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="mcp-client", daemon=True
        )
        self._thread.start()
        self._connections = tuple(
            _ServerConnection(config, self._loop) for config in self._servers
        )
        for connection in self._connections:
            asyncio.run_coroutine_threadsafe(connection.run(), self._loop)
        for connection in self._connections:
            if not connection.ready.wait(timeout):
                _log.warning(
                    "MCP server %r connection timed out after %.0fs",
                    connection.config.name,
                    timeout,
                )

    @property
    def tools(self) -> tuple[Tool, ...]:
        return tuple(
            MCPTool(
                name=tool_full_name(connection.config.name, remote_tool.name),
                description=remote_tool.description or "",
                parameters=dict(remote_tool.inputSchema),
                remote_name=remote_tool.name,
                channel=connection,
            )
            for connection in self._connections
            if connection.session is not None
            for remote_tool in connection.remote_tools
        )

    def close(self) -> None:
        """Stop all server tasks and the loop thread; safe to call repeatedly."""
        for connection in self._connections:
            connection.closed = True  # reject new calls before the unwind starts
        loop = self._loop
        thread = self._thread
        if loop is None or thread is None:
            return
        for connection in self._connections:
            loop.call_soon_threadsafe(connection.request_stop)
        for connection in self._connections:
            connection.done.wait(_CLOSE_TIMEOUT_SECONDS)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(_CLOSE_TIMEOUT_SECONDS)
        if not thread.is_alive():
            loop.close()
        self._loop = None
        self._thread = None
