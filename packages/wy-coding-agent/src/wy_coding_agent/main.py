"""Application entry point: bootstrap the agent, then launch the TUI."""

import argparse
from collections.abc import Sequence

from wy_core import ModelError

from wy_coding_agent.config import ConfigError
from wy_coding_agent.factory import bootstrap
from wy_coding_agent.session import SessionError
from wy_coding_agent.tui.app import ChatApp


def main(argv: Sequence[str] | None = None) -> None:
    """Assemble via the agent layer and start the display."""
    parser = argparse.ArgumentParser(description="Wy Coding Agent TUI")
    parser.add_argument(
        "session_id",
        nargs="?",
        help="要继续的会话 UUID；省略时创建新会话",
    )
    args = parser.parse_args(argv)

    try:
        agent = bootstrap(args.session_id)
    except (ConfigError, ModelError, SessionError) as exc:
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
