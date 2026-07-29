"""config.toml 解析:[realtime] 与 [[mcp.servers]]。

[[mcp.servers]] 的解析与 wy-coding-agent 的同名实现保持同构(复刻件),
两侧修改需互相评估。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import tomllib

_MODES = ("server_vad", "smart_turn")


class ConfigError(RuntimeError):
    """Raised when the application configuration cannot be loaded."""


@dataclass(frozen=True, slots=True)
class RealtimeConfig:
    """实时语音模型的会话参数;url 为 wss base(不含 ?model= 查询串)。"""

    url: str
    api_key: str
    model: str
    voice: str = "longanqian"
    instructions: str = ""
    mode: str = "server_vad"  # "server_vad" | "smart_turn"
    vad_threshold: float = 0.5  # 仅 server_vad,[-1.0, 1.0]
    vad_silence_ms: int = 800  # 仅 server_vad,[200, 6000]
    echo_suppression: bool = True  # True=AI 说话时闭麦;False=耳机模式,支持打断
    max_history_turns: int = 20  # 1-50


def load_realtime_config(path: Path | None = None) -> RealtimeConfig:
    """Load and validate the [realtime] section of config.toml."""
    path = path if path is not None else Path.cwd() / "config.toml"
    try:
        with path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except FileNotFoundError as exc:
        raise ConfigError(
            f"配置文件不存在：{path}。请复制 config.example.toml 并填写配置。"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"配置文件格式错误：{exc}") from exc

    realtime_config = config.get("realtime")
    if not isinstance(realtime_config, dict):
        raise ConfigError("配置文件缺少 [realtime] 配置段。")

    values: dict[str, str] = {}
    for field_name in ("url", "api_key", "model"):
        value = realtime_config.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"配置项 realtime.{field_name} 不能为空。")
        values[field_name] = value.strip()

    if values["api_key"] == "your-api-key":
        raise ConfigError("请先在 config.toml 中填写真实的 realtime.api_key。")

    defaults = RealtimeConfig(url="", api_key="", model="")
    voice = realtime_config.get("voice", defaults.voice)
    instructions = realtime_config.get("instructions", defaults.instructions)
    if not isinstance(voice, str) or not voice.strip():
        raise ConfigError("配置项 realtime.voice 不能为空。")
    if not isinstance(instructions, str):
        raise ConfigError("配置项 realtime.instructions 必须是字符串。")

    mode = realtime_config.get("mode", defaults.mode)
    if mode not in _MODES:
        raise ConfigError(f"配置项 realtime.mode 必须是 {' 或 '.join(map(repr, _MODES))}。")

    vad_threshold = realtime_config.get("vad_threshold", defaults.vad_threshold)
    if isinstance(vad_threshold, bool) or not isinstance(vad_threshold, int | float):
        raise ConfigError("配置项 realtime.vad_threshold 必须是数字。")
    if not -1.0 <= vad_threshold <= 1.0:
        raise ConfigError("配置项 realtime.vad_threshold 取值范围是 [-1.0, 1.0]。")

    vad_silence_ms = realtime_config.get("vad_silence_ms", defaults.vad_silence_ms)
    if isinstance(vad_silence_ms, bool) or not isinstance(vad_silence_ms, int):
        raise ConfigError("配置项 realtime.vad_silence_ms 必须是整数。")
    if not 200 <= vad_silence_ms <= 6000:
        raise ConfigError("配置项 realtime.vad_silence_ms 取值范围是 [200, 6000]。")

    echo_suppression = realtime_config.get("echo_suppression", defaults.echo_suppression)
    if not isinstance(echo_suppression, bool):
        raise ConfigError("配置项 realtime.echo_suppression 必须是布尔值。")

    max_history_turns = realtime_config.get("max_history_turns", defaults.max_history_turns)
    if isinstance(max_history_turns, bool) or not isinstance(max_history_turns, int):
        raise ConfigError("配置项 realtime.max_history_turns 必须是整数。")
    if not 1 <= max_history_turns <= 50:
        raise ConfigError("配置项 realtime.max_history_turns 取值范围是 [1, 50]。")

    return RealtimeConfig(
        **values,
        voice=voice.strip(),
        instructions=instructions,
        mode=mode,
        vad_threshold=float(vad_threshold),
        vad_silence_ms=vad_silence_ms,
        echo_suppression=echo_suppression,
        max_history_turns=max_history_turns,
    )


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    name: str
    transport: str  # "stdio" | "http"
    command: str = ""  # stdio 必填
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    url: str = ""  # http 必填
    headers: Mapping[str, str] = field(default_factory=dict)


def _parse_str_table(raw: object, label: str) -> dict[str, str]:
    """Validate an optional table of string keys/values (env, headers)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in raw.items()
    ):
        raise ConfigError(f"配置项 {label} 必须是字符串到字符串的表。")
    return dict(raw)


def _parse_mcp_server(raw: object, index: int) -> MCPServerConfig:
    label = f"mcp.servers[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"配置项 {label} 必须是表。")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"配置项 {label}.name 不能为空。")
    transport = raw.get("transport")
    if transport not in ("stdio", "http"):
        raise ConfigError(f"配置项 {label}.transport 必须是 \"stdio\" 或 \"http\"。")

    command = raw.get("command", "")
    args = raw.get("args", [])
    url = raw.get("url", "")
    if transport == "stdio":
        if not isinstance(command, str) or not command.strip():
            raise ConfigError(f"配置项 {label}.command 不能为空（stdio transport 必填）。")
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ConfigError(f"配置项 {label}.args 必须是字符串数组。")
    else:
        if not isinstance(url, str) or not url.strip():
            raise ConfigError(f"配置项 {label}.url 不能为空（http transport 必填）。")

    return MCPServerConfig(
        name=name.strip(),
        transport=transport,
        command=command.strip() if isinstance(command, str) else "",
        args=tuple(args) if isinstance(args, list) else (),
        env=_parse_str_table(raw.get("env"), f"{label}.env"),
        url=url.strip() if isinstance(url, str) else "",
        headers=_parse_str_table(raw.get("headers"), f"{label}.headers"),
    )


def load_mcp_config(path: Path) -> tuple[MCPServerConfig, ...]:
    """Parse the [[mcp.servers]] tables from a TOML MCP config file.

    The file must exist and parse; a file without an [mcp] section configures
    no servers (the same file may hold unrelated sections, e.g. the host's
    config.toml).
    """
    try:
        with path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except FileNotFoundError as exc:
        raise ConfigError(f"MCP 配置文件不存在：{path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"MCP 配置文件解析失败：{path}（{exc}）") from exc

    mcp_config = config.get("mcp")
    if not isinstance(mcp_config, dict):
        return ()
    servers_raw = mcp_config.get("servers")
    if servers_raw is None:
        return ()
    if not isinstance(servers_raw, list):
        raise ConfigError("配置项 mcp.servers 必须是表数组（[[mcp.servers]]）。")

    servers = tuple(
        _parse_mcp_server(raw, index) for index, raw in enumerate(servers_raw)
    )
    names = [server.name for server in servers]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise ConfigError(f"配置项 mcp.servers 存在重复的 name：{sorted(duplicates)}")
    return servers
