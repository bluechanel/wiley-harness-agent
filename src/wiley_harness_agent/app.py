"""Application entry point: assemble the agent services and launch the TUI."""

import argparse
from collections.abc import Sequence

from wiley_harness_agent.agent import (
    ChatService,
    ConfigError,
    ConversationService,
    SessionError,
    SessionStore,
    load_config,
)
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
        session = SessionStore(args.session_id)
    except SessionError as exc:
        print(f"启动失败：{exc}")
        return

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"启动失败：{exc}")
        print(f"session_id: {session.session_id}")
        return

    chat = ChatService(
        config,
        messages=session.conversation_messages(),
        total_usage=session.total_usage,
    )
    conversation = ConversationService(chat, session)

    try:
        ChatApp(
            conversation,
            session_id=session.session_id,
            history=session.records,
        ).run()
    finally:
        print(f"resume agent: uv run main.py {session.session_id}")
