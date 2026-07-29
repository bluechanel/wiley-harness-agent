"""config.toml 解析:[anthropic]、[compaction]、[skills] 与 [[mcp.servers]]。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
import tomllib


class ConfigError(RuntimeError):
    """Raised when the application configuration cannot be loaded."""


@dataclass(frozen=True, slots=True)
class AnthropicConfig:
    api_key: str
    base_url: str
    model: str
    max_tokens: int = 8192
    thinking_budget_tokens: int = 4096


@dataclass(frozen=True, slots=True)
class CompactionConfig:
    """自动上下文压缩参数,默认值与 wy_core.Session 一致。"""

    max_context_tokens: int = 150_000
    keep_recent: int = 8


def load_config(path: Path | None = None) -> AnthropicConfig:
    """Load and validate the [anthropic] section of config.toml."""
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

    anthropic_config = config.get("anthropic")
    if not isinstance(anthropic_config, dict):
        raise ConfigError("配置文件缺少 [anthropic] 配置段。")

    values: dict[str, str] = {}
    for field_name in ("api_key", "base_url", "model"):
        value = anthropic_config.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"配置项 anthropic.{field_name} 不能为空。")
        values[field_name] = value.strip()

    if values["api_key"] == "your-api-key":
        raise ConfigError("请先在 config.toml 中填写真实的 anthropic.api_key。")

    max_tokens = anthropic_config.get("max_tokens", 8192)
    thinking_budget_tokens = anthropic_config.get("thinking_budget_tokens", 4096)
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        raise ConfigError("配置项 anthropic.max_tokens 必须是正整数。")
    if not isinstance(thinking_budget_tokens, int) or thinking_budget_tokens < 0:
        raise ConfigError(
            "配置项 anthropic.thinking_budget_tokens 必须是非负整数。"
        )
    if 0 < thinking_budget_tokens < 1024:
        raise ConfigError(
            "启用扩展思考时，anthropic.thinking_budget_tokens 不能小于 1024。"
        )
    if thinking_budget_tokens >= max_tokens:
        raise ConfigError(
            "配置项 anthropic.thinking_budget_tokens 必须小于 max_tokens。"
        )

    return AnthropicConfig(
        **values,
        max_tokens=max_tokens,
        thinking_budget_tokens=thinking_budget_tokens,
    )


def load_compaction_config(path: Path | None = None) -> CompactionConfig:
    """Load the optional [compaction] section; missing file/section means defaults."""
    path = path if path is not None else Path.cwd() / "config.toml"
    try:
        with path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return CompactionConfig()

    compaction_config = config.get("compaction")
    if not isinstance(compaction_config, dict):
        return CompactionConfig()

    defaults = CompactionConfig()
    max_context_tokens = compaction_config.get(
        "max_context_tokens", defaults.max_context_tokens
    )
    keep_recent = compaction_config.get("keep_recent", defaults.keep_recent)
    if not isinstance(max_context_tokens, int) or max_context_tokens <= 0:
        raise ConfigError("配置项 compaction.max_context_tokens 必须是正整数。")
    if not isinstance(keep_recent, int) or keep_recent < 1:
        raise ConfigError("配置项 compaction.keep_recent 必须是不小于 1 的整数。")
    return CompactionConfig(
        max_context_tokens=max_context_tokens, keep_recent=keep_recent
    )


def load_skills_config(path: Path | None = None) -> tuple[Path, ...] | None:
    """Load the optional [skills] section: the skill directory search path.

    缺文件/缺段/缺 dirs 返回 None(调用方用官方默认目录);``dirs = []``
    显式关闭 skills。目录顺序即优先级(同名 skill 先者胜),``~`` 展开,
    相对路径按调用方 CWD 解析。
    """
    path = path if path is not None else Path.cwd() / "config.toml"
    try:
        with path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return None

    skills_config = config.get("skills")
    if not isinstance(skills_config, dict):
        return None
    dirs = skills_config.get("dirs")
    if dirs is None:
        return None
    if not isinstance(dirs, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in dirs
    ):
        raise ConfigError("配置项 skills.dirs 必须是非空字符串数组。")
    return tuple(Path(entry.strip()).expanduser() for entry in dirs)


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
