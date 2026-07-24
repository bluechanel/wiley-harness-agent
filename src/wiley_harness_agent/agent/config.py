from dataclasses import dataclass
from pathlib import Path
import tomllib


DEFAULT_CONFIG_PATH = Path.cwd() / "config.toml"


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
class DebugConfig:
    enabled: bool = False


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AnthropicConfig:
    """Load and validate the local Anthropic configuration."""
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
    for field in ("api_key", "base_url", "model"):
        value = anthropic_config.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"配置项 anthropic.{field} 不能为空。")
        values[field] = value.strip()

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


def load_debug_config(path: Path = DEFAULT_CONFIG_PATH) -> DebugConfig:
    """Load the optional [debug] section; missing file/section means disabled."""
    try:
        with path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return DebugConfig()

    debug_config = config.get("debug")
    if not isinstance(debug_config, dict):
        return DebugConfig()

    enabled = debug_config.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("配置项 debug.enabled 必须是布尔值。")
    return DebugConfig(enabled=enabled)
