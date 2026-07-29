"""Application entry point: bootstrap the realtime agent and run the console loop."""

import argparse
import asyncio
import json
from collections.abc import Sequence

from wy_core import ToolCall, ToolResult

from wy_realtime_agent.agent import (
    AssistantTranscript,
    Interrupted,
    RealtimeAgent,
    SessionEnded,
    UserTranscript,
)
from wy_realtime_agent.config import ConfigError
from wy_realtime_agent.factory import bootstrap


async def _run(agent: RealtimeAgent) -> None:
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
    """Assemble via the factory and run the console conversation loop."""
    parser = argparse.ArgumentParser(description="Wy Realtime Voice Agent")
    parser.parse_args(argv)

    try:
        agent = bootstrap()
    except ConfigError as exc:
        print(f"启动失败：{exc}")
        return

    print("连接实时语音模型…对着麦克风说话即可对话,按 Ctrl+C 退出。")
    try:
        asyncio.run(_run(agent))
    except KeyboardInterrupt:
        print("\n对话结束")
    finally:
        agent.close()
