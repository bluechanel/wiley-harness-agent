"""RealtimeApp TUI 冒烟:经假 WebSocket 全链路驱动,校验流式转写的最终展示。

纯渲染辅助(user_text/assistant_text 等)直接断言 rich Text;App 行为用
Textual 的 headless ``run_test`` 驱动(仓库约定不引入 pytest-asyncio,
外层 ``asyncio.run`` 包装)。增量透传与打断抑制的编排语义在 wy-core
用例覆盖,这里只验证展示层:多个增量落在同一行、完成事件定格最终
文本、打断在行尾留标记、会话结束提示成行。
"""

import asyncio
import time

from textual.containers import VerticalScroll

from wy_realtime_agent.tui import (
    RealtimeApp,
    assistant_text,
    status_text,
    system_text,
    tool_call_text,
    tool_result_text,
    user_text,
)

from realtime_helpers import make_agent

_TIMEOUT_SECONDS = 5.0


def test_render_helpers_compose_prefixed_lines() -> None:
    assert user_text("你好", "世界").plain == "[你] 你好世界"
    assert user_text("你好").plain == "[你] 你好"
    assert assistant_text("在的").plain == "[AI] 在的"
    assert assistant_text("早上", interrupted=True).plain == "[AI] 早上 (已打断)"
    assert tool_call_text("read", {"path": "a.txt"}).plain == '[工具] read {"path": "a.txt"}'
    assert tool_result_text("read", is_error=False).plain == "[工具] read 完成"
    assert tool_result_text("read", is_error=True).plain == "[工具] read 失败"
    assert system_text("(已打断)").plain == "(已打断)"


def test_status_text_renders_state_badge_with_optional_detail() -> None:
    assert status_text("listening").plain == "● 听"
    assert status_text("thinking").plain == "● 思考"
    assert status_text("speaking").plain == "● 说"
    assert status_text("tool", "read").plain == "● 工具执行 · read"
    assert status_text("ended", "x · 按 q 退出").plain == "● 已结束 · x · 按 q 退出"


def _transcript_lines(app: RealtimeApp) -> list[str]:
    transcript = app.query_one("#transcript", VerticalScroll)
    return [line.content.plain for line in transcript.children]


async def _wait_for_session_end(pilot, app: RealtimeApp) -> None:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while True:
        lines = _transcript_lines(app)
        if lines and lines[-1].startswith("会话结束"):
            return
        if time.monotonic() > deadline:
            raise AssertionError(f"等待会话结束超时,当前行:{lines}")
        await pilot.pause(0.01)


def _run_app_to_session_end(agent) -> tuple[list[str], str]:
    """headless 跑 App 至会话结束行出现,返回(对话区各行纯文本, 状态栏纯文本)。"""

    async def go() -> tuple[list[str], str]:
        app = RealtimeApp(agent)
        async with app.run_test() as pilot:
            await _wait_for_session_end(pilot, app)
            return _transcript_lines(app), app.query_one("#status").content.plain

    return asyncio.run(go())


def test_tui_streams_each_utterance_into_a_single_line() -> None:
    agent, _ws = make_agent(
        [
            {"type": "session.updated", "session": {}},
            {"type": "input_audio_buffer.speech_started"},
            {
                "type": "conversation.item.input_audio_transcription.delta",
                "text": "你",
                "stash": "好",
            },
            {"type": "conversation.item.input_audio_transcription.delta", "text": "好"},
            {"type": "input_audio_buffer.speech_stopped"},
            {"type": "input_audio_buffer.committed", "item_id": "item_0"},
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "你好",
            },
            {"type": "response.created", "response": {"id": "resp_1"}},
            {"type": "response.audio_transcript.delta", "delta": "你好"},
            {"type": "response.audio_transcript.delta", "delta": "呀"},
            {"type": "response.audio_transcript.done", "transcript": "你好呀"},
            {"type": "response.done", "response": {"status": "completed"}},
        ]
    )

    lines, status = _run_app_to_session_end(agent)

    # 每段话语只占一行:增量在行内累积,完成事件定格为最终转写;
    # 生命周期事件(session.updated/speech_started…)只驱动状态徽章,不产生行。
    assert lines == ["[你] 你好", "[AI] 你好呀", "会话结束:服务端关闭了连接"]
    assert status == "● 已结束 · 服务端关闭了连接 · 按 q 退出"


def test_tui_marks_interrupted_response_in_place() -> None:
    agent, _ws = make_agent(
        [
            {"type": "response.created", "response": {"id": "resp_1"}},
            {"type": "response.audio_transcript.delta", "delta": "早上"},
            {"type": "input_audio_buffer.speech_started"},
            # 被打断响应的残余字幕由编排层抑制,不应出现在界面上。
            {"type": "response.audio_transcript.delta", "delta": "好呀"},
            {"type": "response.done", "response": {"status": "cancelled"}},
        ]
    )

    lines, _status = _run_app_to_session_end(agent)

    assert lines == ["[AI] 早上 (已打断)", "会话结束:服务端关闭了连接"]
