"""Tests for the MCP bridge (复刻件): lifecycle, tool bridging, factory wiring."""

import sys
import textwrap
from pathlib import Path

import pytest

from wy_realtime_agent.config import ConfigError, MCPServerConfig
from wy_realtime_agent.factory import create_agent
from wy_realtime_agent.mcp import MCPClientManager, tool_full_name

from realtime_helpers import make_config


def test_tool_full_name_uses_double_underscore_prefix() -> None:
    assert tool_full_name("fetch", "get_page") == "mcp__fetch__get_page"


# --- end-to-end against a real stdio server ---

_SERVER_SCRIPT = """
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")

@mcp.tool()
def add(a: int, b: int) -> int:
    \"\"\"Add two integers.\"\"\"
    return a + b

@mcp.tool()
def boom() -> str:
    \"\"\"Always fails.\"\"\"
    raise RuntimeError("kaboom")

mcp.run()
"""


@pytest.fixture
def manager() -> MCPClientManager:
    servers = (
        MCPServerConfig(
            name="demo",
            transport="stdio",
            command=sys.executable,
            args=("-c", _SERVER_SCRIPT),
        ),
    )
    mgr = MCPClientManager(servers)
    mgr.start(timeout=30)
    yield mgr
    mgr.close()


def test_manager_exposes_prefixed_tools_and_executes_calls(
    manager: MCPClientManager,
) -> None:
    tools = {tool.name: tool for tool in manager.tools}

    assert set(tools) == {"mcp__demo__add", "mcp__demo__boom"}
    add = tools["mcp__demo__add"]
    assert add.description == "Add two integers."
    assert add.parameters["type"] == "object"
    assert "a" in add.parameters["properties"]

    assert add.execute({"a": 2, "b": 3}) == "5"


def test_manager_raises_on_server_side_tool_error(manager: MCPClientManager) -> None:
    boom = {tool.name: tool for tool in manager.tools}["mcp__demo__boom"]

    with pytest.raises(RuntimeError, match="kaboom"):
        boom.execute({})


def test_manager_close_is_idempotent_and_disables_tools(
    manager: MCPClientManager,
) -> None:
    add = {tool.name: tool for tool in manager.tools}["mcp__demo__add"]

    manager.close()
    manager.close()

    with pytest.raises(RuntimeError, match="not connected"):
        add.execute({"a": 1, "b": 1})


def test_manager_skips_unreachable_server_without_raising() -> None:
    servers = (
        MCPServerConfig(
            name="broken",
            transport="stdio",
            command="/nonexistent/definitely-not-a-binary",
        ),
    )
    mgr = MCPClientManager(servers)
    try:
        mgr.start(timeout=15)
        assert mgr.tools == ()
    finally:
        mgr.close()


# --- create_agent wiring ---


def test_create_agent_converts_mcp_config_servers_into_tools(tmp_path: Path) -> None:
    script_path = tmp_path / "server.py"
    script_path.write_text(_SERVER_SCRIPT, encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        textwrap.dedent(
            f"""
            [[mcp.servers]]
            name = "demo"
            transport = "stdio"
            command = "{sys.executable}"
            args = ["{script_path}"]
            """
        ),
        encoding="utf-8",
    )

    agent = create_agent(
        config=make_config(),
        tools=(),
        mcp_config=config_path,
        audit=False,
    )
    try:
        assert set(agent.tools) == {"mcp__demo__add", "mcp__demo__boom"}
    finally:
        agent.close()
        agent.close()  # 幂等


def test_create_agent_rejects_missing_mcp_config_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        create_agent(
            config=make_config(),
            tools=(),
            mcp_config=tmp_path / "absent.toml",
            audit=False,
        )


def test_create_agent_defaults_to_read_tool_without_mcp() -> None:
    agent = create_agent(config=make_config(), audit=False)

    assert list(agent.tools) == ["read"]
