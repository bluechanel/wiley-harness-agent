"""Application entry point: create the agent via its factory and launch the TUI."""

import argparse
from collections.abc import Sequence

from wiley_agent import ConfigError, SessionError, create_agent
from wiley_harness_agent.tui import ChatApp


def main(argv: Sequence[str] | None = None) -> None:
    """Build and start the application."""
    parser = argparse.ArgumentParser(description="Wiley Harness Agent")
    parser.add_argument(
        "session_id",
        nargs="?",
        help="要继续的会话 UUID；省略时创建新会话",
    )
    args = parser.parse_args(argv)

    try:
        agent = create_agent(args.session_id)
    except (ConfigError, SessionError) as exc:
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
