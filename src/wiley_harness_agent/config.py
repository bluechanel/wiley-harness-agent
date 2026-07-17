from dataclasses import dataclass
from pathlib import Path
import tomllib


DEFAULT_CONFIG_PATH = Path.cwd() / "config.toml"


class ConfigError(RuntimeError):
    """Raised when the application configuration cannot be loaded."""


@dataclass(frozen=True, slots=True)
class OpenAIConfig:
    api_key: str
    base_url: str
    model: str


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> OpenAIConfig:
    """Load and validate the local OpenAI configuration."""
    try:
        with path.open("rb") as config_file:
            config = tomllib.load(config_file)
    except FileNotFoundError as exc:
        raise ConfigError(
            f"配置文件不存在：{path}。请复制 config.example.toml 并填写配置。"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"配置文件格式错误：{exc}") from exc

    openai_config = config.get("openai")
    if not isinstance(openai_config, dict):
        raise ConfigError("配置文件缺少 [openai] 配置段。")

    values: dict[str, str] = {}
    for field in ("api_key", "base_url", "model"):
        value = openai_config.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"配置项 openai.{field} 不能为空。")
        values[field] = value.strip()

    if values["api_key"] == "your-api-key":
        raise ConfigError("请先在 config.toml 中填写真实的 openai.api_key。")

    return OpenAIConfig(**values)

