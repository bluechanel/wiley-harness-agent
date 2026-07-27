"""Tests for the MCP client: config parsing, tool bridging, lifecycle, factory wiring."""

import sys
import textwrap
from pathlib import Path

import pytest

from wy_core import Model

from wy_coding_agent import create_agent
from wy_coding_agent.config import (
    ConfigError,
    MCPServerConfig,
    load_mcp_config,
)
from wy_coding_agent.mcp import (
    MCPClientManager,
    result_to_text,
    tool_full_name,
)

# --- config parsing ---


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(textwrap.dedent(body), encoding="utf-8")
    return config_path


def test_load_mcp_config_missing_section_means_no_servers(tmp_path: Path) -> None:
    assert load_mcp_config(_write_config(tmp_path, "[compaction]\nkeep_recent = 2\n")) == ()


def test_load_mcp_config_missing_or_broken_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_mcp_config(tmp_path / "absent.toml")
    with pytest.raises(ConfigError):
        load_mcp_config(_write_config(tmp_path, "[mcp\n"))


def test_load_mcp_config_parses_stdio_and_http_servers(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        """
        [[mcp.servers]]
        name = "fetch"
        transport = "stdio"
        command = "uvx"
        args = ["mcp-server-fetch"]
        env = { KEY = "value" }

        [[mcp.servers]]
        name = "docs"
        transport = "http"
        url = "https://example.com/mcp"
        headers = { Authorization = "Bearer xxx" }
        """,
    )

    servers = load_mcp_config(config_path)

    assert servers == (
        MCPServerConfig(
            name="fetch",
            transport="stdio",
            command="uvx",
            args=("mcp-server-fetch",),
            env={"KEY": "value"},
        ),
        MCPServerConfig(
            name="docs",
            transport="http",
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer xxx"},
        ),
    )


@pytest.mark.parametrize(
    "body",
    [
        "[mcp]\nservers = 1\n",
        '[[mcp.servers]]\ntransport = "stdio"\ncommand = "uvx"\n',  # missing name
        '[[mcp.servers]]\nname = "a"\ntransport = "ftp"\n',  # unknown transport
        '[[mcp.servers]]\nname = "a"\ntransport = "stdio"\n',  # missing command
        '[[mcp.servers]]\nname = "a"\ntransport = "http"\n',  # missing url
        (
            '[[mcp.servers]]\nname = "a"\ntransport = "stdio"\ncommand = "x"\n'
            'args = [1]\n'
        ),
        (
            '[[mcp.servers]]\nname = "a"\ntransport = "stdio"\ncommand = "x"\n'
            '[[mcp.servers]]\nname = "a"\ntransport = "http"\nurl = "http://x"\n'
        ),  # duplicate name
    ],
)
def test_load_mcp_config_rejects_invalid_entries(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigError):
        load_mcp_config(_write_config(tmp_path, body))


# --- pure helpers ---


def test_tool_full_name_uses_double_underscore_prefix() -> None:
    assert tool_full_name("fetch", "get_page") == "mcp__fetch__get_page"


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Result:
    def __init__(self, content: list, structured: object = None) -> None:
        self.content = content
        self.structuredContent = structured
        self.isError = False


def test_result_to_text_joins_text_blocks() -> None:
    assert result_to_text(_Result([_Block("a"), _Block("b")])) == "a\nb"


def test_result_to_text_falls_back_to_structured_content() -> None:
    assert result_to_text(_Result([], structured={"answer": 3})) == '{"answer": 3}'


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


class _StubModel(Model):
    name = "stub"

    async def stream(self, messages, *, system=None, tools=None):
        raise AssertionError("model should not be called in these tests")
        yield  # unreachable: marks this method as an async generator


def test_create_agent_converts_mcp_config_servers_into_tools(tmp_path: Path) -> None:
    script_path = tmp_path / "server.py"
    script_path.write_text(_SERVER_SCRIPT, encoding="utf-8")
    config_path = _write_config(
        tmp_path,
        f"""
        [[mcp.servers]]
        name = "demo"
        transport = "stdio"
        command = "{sys.executable}"
        args = ["{script_path}"]
        """,
    )

    agent = create_agent(
        model=_StubModel(),
        tools=(),
        mcp_config=config_path,
        sessions_dir=tmp_path / "sessions",
    )
    try:
        assert set(agent._agent.tools) == {"mcp__demo__add", "mcp__demo__boom"}
    finally:
        agent.close()


def test_create_agent_rejects_missing_mcp_config_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        create_agent(
            model=_StubModel(),
            tools=(),
            mcp_config=tmp_path / "absent.toml",
            sessions_dir=tmp_path / "sessions",
        )
