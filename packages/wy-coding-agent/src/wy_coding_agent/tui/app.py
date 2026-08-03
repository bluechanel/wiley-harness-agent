"""Textual chat application. Displays what the agent layer streams and records."""

from typing import AsyncIterator, Protocol

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Collapsible, Footer, Header, Input, Markdown, Static

from wy_core import (
    AgentEvent,
    Compaction,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolResult,
    TurnEnd,
    Usage,
)

from wy_coding_agent.reminders import PlanModeState
from wy_coding_agent.session import SessionRecord
import wy_coding_agent.tui.render as render


class ChatBackend(Protocol):
    plan_mode: PlanModeState | None

    def stream(self, user_input: str) -> AsyncIterator[AgentEvent]: ...

    def save_state(self) -> None: ...


class ChatApp(App[None]):
    """Textual chat interface with Markdown rendering."""

    CSS = """
    Screen {
        background: $surface;
    }

    #conversation {
        height: 1fr;
        border: round $primary;
        padding: 1 2 0 2;
    }

    #message-list {
        min-height: 100%;
        height: auto;
        align: left bottom;
    }

    #message-list > Markdown {
        margin: 0 0 1 0;
    }

    #message-list > Collapsible {
        margin: 0 0 1 0;
        border-top: none;
        padding-bottom: 0;
    }

    #message-list > Collapsible.reasoning Markdown {
        color: #9A9A9A;
    }

    #message-list > Collapsible.tool Markdown {
        color: #8A8A8A;
    }

    #prompt {
        margin: 1 0 0 0;
        border: round $accent;
    }

    #status-bar {
        height: 1;
        margin: 0 1;
    }

    #status {
        width: 1fr;
        color: $text-muted;
    }

    #usage {
        width: auto;
        color: $text-muted;
    }
    """

    TITLE = "Wy Coding Agent"
    SUB_TITLE = "Markdown chat · 输入 exit 或 quit 退出"

    def __init__(
        self,
        chat: ChatBackend,
        *,
        session_id: str = "",
        history: tuple[SessionRecord, ...] = (),
        total_usage: Usage | None = None,
        context_tokens: int = 0,
    ) -> None:
        super().__init__()
        self.chat = chat
        self.session_id = session_id
        self.history = history
        self.total_usage = total_usage if total_usage is not None else Usage()
        self.context_tokens = context_tokens

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            with VerticalScroll(id="conversation"):
                yield Vertical(id="message-list")
            yield Input(
                placeholder="输入消息（支持 Markdown），按 Enter 发送…",
                id="prompt",
            )
            with Horizontal(id="status-bar"):
                yield Static("就绪", id="status")
                yield Static(
                    render.usage_bar_text(self.total_usage, self.context_tokens),
                    id="usage",
                )
        yield Footer()

    def on_mount(self) -> None:
        self._prompt.focus()
        if self.session_id:
            self.sub_title = f"Session {self.session_id}"
        self.call_after_refresh(
            self._render_history,
        )

    async def _render_history(self) -> None:
        if not self.history:
            await self._mount_view(render.WELCOME_VIEW)
            return

        for record in self.history:
            for view in render.render_record(record):
                await self._mount_view(view)

        self._scroll_to_bottom()

    @property
    def _prompt(self) -> Input:
        return self.query_one("#prompt", Input)

    @property
    def _status(self) -> Static:
        return self.query_one("#status", Static)

    @property
    def _usage(self) -> Static:
        return self.query_one("#usage", Static)

    @property
    def _message_list(self) -> Vertical:
        return self.query_one("#message-list", Vertical)

    async def _mount_view(self, view: render.MessageView) -> Markdown:
        """Mount one view; collapsible views start collapsed. Returns the
        inner Markdown widget so streamed deltas can keep updating it."""
        if view.collapsible_title:
            body = Markdown(view.markdown)
            await self._message_list.mount(
                Collapsible(
                    body,
                    title=view.collapsible_title,
                    collapsed=True,
                    classes=view.classes,
                )
            )
            self._scroll_to_bottom()
            return body
        return await self._mount_markdown(view.markdown, classes=view.classes)

    async def _mount_markdown(self, content: str, *, classes: str = "") -> Markdown:
        widget = Markdown(content, classes=classes)
        await self._message_list.mount(widget)
        self._scroll_to_bottom()
        return widget

    async def _update_markdown(self, widget: Markdown, content: str) -> None:
        await widget.update(content)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        self.query_one("#conversation", VerticalScroll).scroll_end(
            animate=False,
            immediate=True,
        )

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        user_input = event.value.strip()
        event.input.value = ""
        if not user_input:
            return
        if user_input.lower() in {"exit", "quit"}:
            self.exit()
            return
        if user_input == "/plan" or user_input.startswith("/plan "):
            user_input = await self._enter_plan_mode(user_input)
            if not user_input:
                return

        await self._mount_markdown(render.user_markdown(user_input))
        self._prompt.disabled = True
        self._status.update("请求中…")

        reasoning_widget: Markdown | None = None
        answer_widget: Markdown | None = None
        reasoning_text = ""
        answer_text = ""

        try:
            async for chunk in self.chat.stream(user_input):
                if isinstance(chunk, ThinkingDelta):
                    if reasoning_widget is None:
                        reasoning_widget = await self._mount_view(
                            render.reasoning_view("")
                        )
                    reasoning_text += chunk.thinking
                    self._status.update("思考中…")
                    await self._update_markdown(reasoning_widget, reasoning_text)
                elif isinstance(chunk, TextDelta):
                    if answer_widget is None:
                        answer_widget = await self._mount_markdown(
                            render.answer_markdown("")
                        )
                    answer_text += chunk.text
                    self._status.update("生成中…")
                    await self._update_markdown(
                        answer_widget,
                        render.answer_markdown(answer_text),
                    )
                elif isinstance(chunk, ToolCall):
                    # 后续轮次的思考/回答另起新块，保持与工具块的时间顺序。
                    reasoning_widget = None
                    answer_widget = None
                    reasoning_text = ""
                    answer_text = ""
                    self._status.update(f"执行工具：{chunk.name}…")
                    await self._mount_view(
                        render.tool_call_view(chunk.name, chunk.input)
                    )
                elif isinstance(chunk, ToolResult):
                    await self._mount_view(
                        render.tool_output_view(
                            chunk.name,
                            chunk.content,
                            is_error=chunk.is_error,
                        )
                    )
                elif isinstance(chunk, Compaction):
                    reasoning_widget = None
                    answer_widget = None
                    reasoning_text = ""
                    answer_text = ""
                    self._status.update("上下文已压缩")
                    await self._mount_view(
                        render.compaction_view(chunk.dropped, chunk.summary)
                    )
                elif isinstance(chunk, TurnEnd):
                    self._usage.update(
                        render.usage_bar_text(chunk.usage, chunk.context_tokens)
                    )
        except Exception as exc:
            await self._mount_markdown(render.error_markdown(exc))
        finally:
            self._prompt.disabled = False
            self._status.update(self._ready_status())
            self._prompt.focus()

    async def _enter_plan_mode(self, command: str) -> str:
        """处理 /plan 本地命令(纯 harness 状态切换,不发给模型);
        返回需要继续作为用户输入发送的剩余参数,可为空。"""
        plan_mode = getattr(self.chat, "plan_mode", None)
        if plan_mode is None:
            await self._mount_markdown(render.error_markdown("当前后端不支持 plan 模式"))
            return ""
        plan_mode.enable()
        saver = getattr(self.chat, "save_state", None)
        if callable(saver):
            saver()  # 回合外的模式切换即时落盘,重启后仍处于 plan 模式
        await self._mount_view(render.PLAN_MODE_VIEW)
        self._status.update(self._ready_status())
        return command[len("/plan"):].strip()

    def _ready_status(self) -> str:
        plan_mode = getattr(self.chat, "plan_mode", None)
        if plan_mode is not None and plan_mode.active:
            return "就绪 · PLAN 模式"
        return "就绪"
