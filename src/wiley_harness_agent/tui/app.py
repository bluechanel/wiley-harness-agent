"""Textual chat application. Displays what the agent layer streams and records."""

from typing import AsyncIterator, Protocol

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Markdown, Static

from wiley_harness_agent.agent import ChatStreamEvent, ChatUsage, SessionRecord
import wiley_harness_agent.tui.render as render


class ChatBackend(Protocol):
    def stream(self, user_input: str) -> AsyncIterator[ChatStreamEvent]: ...


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

    #message-list > Markdown.reasoning {
        color: #9A9A9A;
    }

    #message-list > Markdown.tool {
        color: #8A8A8A;
        border-left: thick #4A4A4A;
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

    TITLE = "Wiley Harness Agent"
    SUB_TITLE = "Markdown chat · 输入 exit 或 quit 退出"

    def __init__(
        self,
        chat: ChatBackend,
        *,
        session_id: str = "",
        history: tuple[SessionRecord, ...] = (),
        total_usage: ChatUsage = ChatUsage(),
        context_tokens: int = 0,
    ) -> None:
        super().__init__()
        self.chat = chat
        self.session_id = session_id
        self.history = history
        self.total_usage = total_usage
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

        await self._mount_markdown(render.user_markdown(user_input))
        self._prompt.disabled = True
        self._status.update("请求中…")

        reasoning_widget: Markdown | None = None
        answer_widget: Markdown | None = None
        reasoning_text = ""
        answer_text = ""

        try:
            async for event in self.chat.stream(user_input):
                if event.kind == "reasoning":
                    if reasoning_widget is None:
                        reasoning_widget = await self._mount_markdown(
                            render.reasoning_markdown(""),
                            classes="reasoning",
                        )
                    reasoning_text += event.text
                    self._status.update("思考中…")
                    await self._update_markdown(
                        reasoning_widget,
                        render.reasoning_markdown(reasoning_text),
                    )
                elif event.kind == "answer":
                    if answer_widget is None:
                        answer_widget = await self._mount_markdown(
                            render.answer_markdown("")
                        )
                    answer_text += event.text
                    self._status.update("生成中…")
                    await self._update_markdown(
                        answer_widget,
                        render.answer_markdown(answer_text),
                    )
                elif event.kind == "tool_call":
                    # 后续轮次的思考/回答另起新块，保持与工具块的时间顺序。
                    reasoning_widget = None
                    answer_widget = None
                    reasoning_text = ""
                    answer_text = ""
                    self._status.update(f"执行工具：{event.tool_name}…")
                    await self._mount_markdown(
                        render.tool_call_markdown(
                            event.tool_name, event.tool_arguments
                        ),
                        classes="tool",
                    )
                elif event.kind == "tool_output":
                    await self._mount_markdown(
                        render.tool_output_markdown(
                            event.tool_name,
                            event.text,
                            is_error=event.tool_is_error,
                        ),
                        classes="tool",
                    )
                elif event.kind == "usage":
                    if event.total_usage:
                        self._usage.update(
                            render.usage_bar_text(
                                event.total_usage, event.context_tokens
                            )
                        )
        except Exception as exc:
            await self._mount_markdown(render.error_markdown(exc))
        finally:
            self._prompt.disabled = False
            self._status.update("就绪")
            self._prompt.focus()
