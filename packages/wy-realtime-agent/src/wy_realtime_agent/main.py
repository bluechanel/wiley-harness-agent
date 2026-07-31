"""Application entry point: bootstrap the realtime agent, launch the TUI or console loop."""

import argparse
import asyncio
import json
from collections.abc import Sequence

from wy_core import (
    AssistantTranscript,
    Interrupted,
    RealtimeAgent,
    SessionEnded,
    ToolCall,
    ToolResult,
    UserTranscript,
)

from wy_realtime_agent.config import ConfigError
from wy_realtime_agent.factory import bootstrap
from wy_realtime_agent.tui import RealtimeApp


async def _run(agent: RealtimeAgent) -> None:
    """纯控制台逐行输出:只打印完成级事件,转写增量静默忽略。"""
    async for event in agent.run():
        match event:
            case UserTranscript(text=text):
                print(f"[你] {text}")
            case AssistantTranscript(text=text):
                print(f"[AI] {text}")
            case ToolCall(name=name, input=tool_input):
                print(f"[工具] {name} {json.dumps(tool_input, ensure_ascii=False)}")
            case ToolResult(name=name, is_error=is_error):
                print(f"[工具] {name} {'失败' if is_error else '完成'}")
            case Interrupted():
                print("(已打断)")
            case SessionEnded(reason=reason):
                print(f"会话结束：{reason}")


def main(argv: Sequence[str] | None = None) -> None:
    """Assemble via the factory, then run the TUI (default) or the plain console loop."""
    parser = argparse.ArgumentParser(description="Wy Realtime Voice Agent")
    parser.add_argument(
        "--plain",
        action="store_true",
        help="纯控制台逐行输出(不启动 TUI,适合调试与重定向)",
    )
    args = parser.parse_args(argv)

    try:
        agent = bootstrap()
    except ConfigError as exc:
        print(f"启动失败：{exc}")
        return

    try:
        if args.plain:
            print("连接实时语音模型…对着麦克风说话即可对话,按 Ctrl+C 退出。")
            asyncio.run(_run(agent))
        else:
            RealtimeApp(agent).run()
    except KeyboardInterrupt:
        print("\n对话结束")
    finally:
        agent.close()
