"""Application entry point: bootstrap the agent, then launch the TUI."""

import argparse
from collections.abc import Sequence

from wiley_agent import ConfigError, ProviderError, SessionError, bootstrap
from wiley_tui.app import ChatApp


def main(argv: Sequence[str] | None = None) -> None:
    """Assemble via the agent package and start the display."""
    parser = argparse.ArgumentParser(description="Wiley Agent TUI")
    parser.add_argument(
        "session_id",
        nargs="?",
        help="要继续的会话 UUID；省略时创建新会话",
    )
    args = parser.parse_args(argv)

    try:
        agent = bootstrap(args.session_id)
    except (ConfigError, ProviderError, SessionError) as exc:
        print(f"启动失败：{exc}")
        return

    try:
        ChatApp(
            agent,
            session_id=agent.session_id,
            history=agent.history,
            total_usage=agent.total_usage,
            context_tokens=agent.last_context_tokens,
        ).run()
    finally:
        agent.close()
        print(f"resume agent: uv run main.py {agent.session_id}")
