from wiley_harness_agent.chat import ChatService
from wiley_harness_agent.config import ConfigError, load_config
from wiley_harness_agent.ui import ChatApp


def main() -> None:
    """Build and start the application."""
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"启动失败：{exc}")
        return

    ChatApp(ChatService(config)).run()

