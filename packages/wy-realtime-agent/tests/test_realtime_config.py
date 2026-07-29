"""Tests for config parsing: the [realtime] section and [[mcp.servers]]."""

import textwrap
from pathlib import Path

import pytest

from wy_realtime_agent.config import (
    ConfigError,
    MCPServerConfig,
    RealtimeConfig,
    load_mcp_config,
    load_realtime_config,
)

_MINIMAL = """
[realtime]
url = "wss://ws.example.test/api-ws/v1/realtime"
api_key = "sk-test"
model = "qwen-audio-3.0-realtime-plus"
"""


def _write_config(tmp_path: Path, body: str) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(textwrap.dedent(body), encoding="utf-8")
    return config_path


def test_load_realtime_config_minimal_uses_defaults(tmp_path: Path) -> None:
    config = load_realtime_config(_write_config(tmp_path, _MINIMAL))

    assert config == RealtimeConfig(
        url="wss://ws.example.test/api-ws/v1/realtime",
        api_key="sk-test",
        model="qwen-audio-3.0-realtime-plus",
    )
    assert config.voice == "longanqian"
    assert config.mode == "server_vad"
    assert config.echo_suppression is True


def test_load_realtime_config_parses_all_fields(tmp_path: Path) -> None:
    config = load_realtime_config(
        _write_config(
            tmp_path,
            """
            [realtime]
            url = "wss://ws.example.test/realtime"
            api_key = "sk-test"
            model = "qwen-audio-3.0-realtime-flash"
            voice = "longanlingxi"
            instructions = "你是语音助手"
            mode = "smart_turn"
            vad_threshold = 0.1
            vad_silence_ms = 500
            echo_suppression = false
            max_history_turns = 50
            """,
        )
    )

    assert config == RealtimeConfig(
        url="wss://ws.example.test/realtime",
        api_key="sk-test",
        model="qwen-audio-3.0-realtime-flash",
        voice="longanlingxi",
        instructions="你是语音助手",
        mode="smart_turn",
        vad_threshold=0.1,
        vad_silence_ms=500,
        echo_suppression=False,
        max_history_turns=50,
    )


def test_load_realtime_config_missing_file_or_section(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_realtime_config(tmp_path / "absent.toml")
    with pytest.raises(ConfigError):
        load_realtime_config(_write_config(tmp_path, "[anthropic]\napi_key = \"x\"\n"))


@pytest.mark.parametrize(
    "override",
    [
        'url = ""',
        'api_key = ""',
        'api_key = "your-api-key"',
        'model = ""',
        'voice = ""',
        "instructions = 1",
        'mode = "manual"',
        "vad_threshold = 2.0",
        'vad_threshold = "high"',
        "vad_silence_ms = 100",
        "vad_silence_ms = true",
        'echo_suppression = "yes"',
        "max_history_turns = 0",
        "max_history_turns = 51",
    ],
)
def test_load_realtime_config_rejects_invalid_values(tmp_path: Path, override: str) -> None:
    key = override.split(" ", 1)[0]
    lines = [
        line if not line.startswith(f"{key} ") else override
        for line in textwrap.dedent(_MINIMAL).strip().splitlines()
    ]
    if not any(line.startswith(f"{key} ") for line in lines):
        lines.append(override)

    with pytest.raises(ConfigError):
        load_realtime_config(_write_config(tmp_path, "\n".join(lines)))


# --- [[mcp.servers]](复刻件的解析行为与 wy-coding-agent 保持一致) ---


def test_load_mcp_config_missing_section_means_no_servers(tmp_path: Path) -> None:
    assert load_mcp_config(_write_config(tmp_path, _MINIMAL)) == ()


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

    assert load_mcp_config(config_path) == (
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
            "args = [1]\n"
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
