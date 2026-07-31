"""Textual TUI:实时对话转写的流式展示层。

只做展示:后台 worker 消费 ``RealtimeAgent.run()`` 事件流,把用户/助手
转写渲染为滚动字幕——增量事件(``UserTranscriptDelta``/
``AssistantTranscriptDelta``)实时刷新当前行,转写完成事件定格为最终
文本,打断在当前行留标记;工具调用/结果、服务端错误与会话结束以弱化
行展示;生命周期事件(SessionReady/SpeechStarted/SpeechStopped/
TurnCommitted/ResponseStarted/ResponseDone/ToolResultsSubmitted)只驱动
状态徽章(连接中/空闲/听/思考/说/工具执行/已结束)。音频播放、工具执行
与协议交互全部在 agent 内部完成,本层不做协议响应;``close()`` 等资源
释放留给宿主 main 的 finally。
"""

from __future__ import annotations

import json

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Static

from wy_core import (
    AssistantTranscript,
    AssistantTranscriptDelta,
    ErrorEvent,
    Interrupted,
    RealtimeAgent,
    ResponseDone,
    ResponseStarted,
    SessionEnded,
    SessionReady,
    SpeechStarted,
    SpeechStopped,
    ToolCall,
    ToolResult,
    ToolResultsSubmitted,
    TurnCommitted,
    TurnDiscarded,
    UserTranscript,
    UserTranscriptDelta,
)

_USER_PREFIX = "[你] "
_ASSISTANT_PREFIX = "[AI] "
_USER_STYLE = "bold cyan"
_ASSISTANT_STYLE = "bold green"

# 状态机词汇:状态名 → (展示标签, 颜色样式)。听/思考/说/工具执行由
# 生命周期事件驱动,见 _consume 的映射。
_STATES = {
    "connecting": ("连接中", "yellow"),
    "idle": ("空闲", "green"),
    "listening": ("听", "bold cyan"),
    "thinking": ("思考", "bold yellow"),
    "speaking": ("说", "bold green"),
    "tool": ("工具执行", "bold magenta"),
    "ended": ("已结束", "red"),
}

_IDLE_HINT = "对着麦克风说话即可对话 · q 退出"


def status_text(state: str, detail: str = "") -> Text:
    """状态栏内容:彩色圆点 + 状态标签 + 弱化的补充信息。"""
    label, style = _STATES[state]
    line = Text()
    line.append("● ", style=style)
    line.append(label, style=style)
    if detail:
        line.append(f" · {detail}", style="dim")
    return line


def user_text(confirmed: str, stash: str = "") -> Text:
    """用户转写行:已确定文本正常显示,暂存尾部弱化(随增量整体替换)。"""
    line = Text(no_wrap=False)
    line.append(_USER_PREFIX, style=_USER_STYLE)
    line.append(confirmed)
    if stash:
        line.append(stash, style="dim italic")
    return line


def assistant_text(text: str, *, interrupted: bool = False) -> Text:
    """助手转写(字幕)行;被打断时在行尾留标记。"""
    line = Text(no_wrap=False)
    line.append(_ASSISTANT_PREFIX, style=_ASSISTANT_STYLE)
    line.append(text)
    if interrupted:
        line.append(" (已打断)", style="dim red")
    return line


def tool_call_text(name: str, tool_input: dict) -> Text:
    """工具调用行:名称 + 入参 JSON(只展示调用,不展示输出内容)。"""
    line = Text(no_wrap=False, style="dim")
    line.append(f"[工具] {name} ")
    line.append(json.dumps(tool_input, ensure_ascii=False))
    return line


def tool_result_text(name: str, is_error: bool) -> Text:
    """工具结果行:只报完成/失败,内容留在审计日志里。"""
    line = Text(style="dim")
    line.append(f"[工具] {name} ")
    if is_error:
        line.append("失败", style="red")
    else:
        line.append("完成")
    return line


def system_text(message: str) -> Text:
    """系统提示行(打断、会话结束、运行异常)。"""
    return Text(message, style="dim")


class RealtimeApp(App[None]):
    """流式字幕界面:滚动对话区 + 状态栏,q / Ctrl+C 退出。"""

    CSS = """
    Screen {
        background: $surface;
    }

    #transcript {
        height: 1fr;
        border: round $primary;
        padding: 1 2;
    }

    #transcript > Static {
        width: 100%;
        margin: 0 0 1 0;
    }

    #status {
        height: 1;
        margin: 0 1;
        color: $text-muted;
    }
    """

    TITLE = "Wy Realtime Agent"
    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("ctrl+c", "quit", "退出", show=False),
    ]

    def __init__(self, agent: RealtimeAgent) -> None:
        super().__init__()
        self.agent = agent

    def compose(self) -> ComposeResult:
        yield Header()
        yield VerticalScroll(id="transcript")
        yield Static(status_text("connecting"), id="status")
        yield Footer()

    def on_mount(self) -> None:
        if self.agent.model.name:
            self.sub_title = self.agent.model.name
        self.run_worker(self._consume(), exclusive=True)

    @property
    def _transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    @property
    def _status(self) -> Static:
        return self.query_one("#status", Static)

    async def _append_line(self, content: Text) -> Static:
        line = Static(content)
        await self._transcript.mount(line)
        self._transcript.scroll_end(animate=False, immediate=True)
        return line

    def _update_line(self, line: Static, content: Text) -> None:
        line.update(content)
        self._transcript.scroll_end(animate=False, immediate=True)

    async def _upsert_line(self, line: Static | None, content: Text) -> Static:
        """当前行存在则原位刷新,否则挂一条新行;返回该行供后续增量更新。"""
        if line is None:
            return await self._append_line(content)
        self._update_line(line, content)
        return line

    async def _consume(self) -> None:
        """消费 agent 事件流:转写维护"当前行",生命周期事件驱动状态徽章。

        增量事件只刷新当前行;完成事件定格并结束当前行,下一段增量另起
        新行。打断把标记写进当前助手行(core 已抑制被打断响应的残余
        增量,标记后不会再被追加)。状态徽章由生命周期事件推进:连接中 →
        空闲 → 听 → 思考 → 说/工具执行 → 空闲;词汇之外的事件(如
        AudioDelta)落入 match 空档,静默忽略。
        """
        user_line: Static | None = None
        assistant_line: Static | None = None
        user_confirmed = ""
        assistant_accum = ""
        try:
            async for event in self.agent.run():
                match event:
                    case SessionReady():
                        self._status.update(status_text("idle", _IDLE_HINT))
                    case SpeechStarted():
                        self._status.update(status_text("listening"))
                    case UserTranscriptDelta(text=text, stash=stash):
                        user_confirmed += text
                        user_line = await self._upsert_line(
                            user_line, user_text(user_confirmed, stash)
                        )
                    case SpeechStopped():
                        self._status.update(status_text("thinking", "识别中"))
                    case UserTranscript(text=text):
                        await self._upsert_line(user_line, user_text(text))
                        user_line, user_confirmed = None, ""
                    case TurnCommitted() | ToolResultsSubmitted():
                        self._status.update(status_text("thinking"))
                    case TurnDiscarded():
                        self._status.update(status_text("idle", _IDLE_HINT))
                    case ResponseStarted():
                        self._status.update(status_text("speaking"))
                    case AssistantTranscriptDelta(text=text):
                        assistant_accum += text
                        assistant_line = await self._upsert_line(
                            assistant_line, assistant_text(assistant_accum)
                        )
                    case AssistantTranscript(text=text):
                        await self._upsert_line(assistant_line, assistant_text(text))
                        assistant_line, assistant_accum = None, ""
                    case ResponseDone(cancelled=False):
                        self._status.update(status_text("idle", _IDLE_HINT))
                    case ToolCall(name=name, input=tool_input):
                        await self._append_line(tool_call_text(name, tool_input))
                        self._status.update(status_text("tool", name))
                    case ToolResult(name=name, is_error=is_error):
                        await self._append_line(tool_result_text(name, is_error))
                    case Interrupted():
                        if assistant_line is not None:
                            self._update_line(
                                assistant_line,
                                assistant_text(assistant_accum, interrupted=True),
                            )
                            assistant_line, assistant_accum = None, ""
                        else:
                            await self._append_line(system_text("(已打断)"))
                    case ErrorEvent(type=error_type, message=message):
                        await self._append_line(system_text(f"[错误] {error_type} {message}"))
                    case SessionEnded(reason=reason):
                        await self._append_line(system_text(f"会话结束:{reason}"))
                        self._status.update(status_text("ended", f"{reason} · 按 q 退出"))
        except Exception as exc:  # 编排层上抛的意外异常,兜底展示而非无声退出
            await self._append_line(system_text(f"运行异常:{exc}"))
            self._status.update(status_text("ended", f"运行异常:{exc} · 按 q 退出"))
