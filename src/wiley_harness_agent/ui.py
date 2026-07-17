from typing import AsyncIterator, Protocol

from textual.app import App, ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Markdown, Static

from wiley_harness_agent.chat import ChatStreamEvent, ChatUsage


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

    #prompt {
        margin: 1 0 0 0;
        border: round $accent;
    }

    #status {
        height: 1;
        margin: 0 1;
        color: $text-muted;
    }

    #message-list > Markdown.usage {
        color: #7A7A7A;
        margin: 0 0 1 0;
    }
    """

    TITLE = "Wiley Harness Agent"
    SUB_TITLE = "Markdown chat · 输入 exit 或 quit 退出"

    def __init__(self, chat: ChatBackend) -> None:
        super().__init__()
        self.chat = chat

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            with VerticalScroll(id="conversation"):
                yield Vertical(id="message-list")
            yield Input(
                placeholder="输入消息（支持 Markdown），按 Enter 发送…",
                id="prompt",
            )
            yield Static("就绪", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._prompt.focus()
        self.call_after_refresh(
            self._mount_markdown,
            "### 助手\n\n你好！我是你的 AI 助手。直接输入消息开始对话。"
        )

    @property
    def _prompt(self) -> Input:
        return self.query_one("#prompt", Input)

    @property
    def _status(self) -> Static:
        return self.query_one("#status", Static)

    @property
    def _message_list(self) -> Vertical:
        return self.query_one("#message-list", Vertical)

    async def _mount_markdown(
        self,
        content: str,
        *,
        reasoning: bool = False,
        classes: str = "",
    ) -> Markdown:
        classes = classes or ("reasoning" if reasoning else "")
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

        await self._mount_markdown(f"### 你\n\n{user_input}")
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
                            "### 思考过程\n\n",
                            reasoning=True,
                        )
                    reasoning_text += event.text
                    self._status.update("思考中…")
                    await self._update_markdown(
                        reasoning_widget,
                        f"### 思考过程\n\n{reasoning_text}",
                    )
                elif event.kind == "answer":
                    if answer_widget is None:
                        answer_widget = await self._mount_markdown("### 助手\n\n")
                    answer_text += event.text
                    self._status.update("生成中…")
                    await self._update_markdown(
                        answer_widget,
                        f"### 助手\n\n{answer_text}",
                    )
                elif event.kind == "usage":
                    if event.usage and event.total_usage:
                        await self._mount_markdown(
                            self._format_usage(event.usage, event.total_usage),
                            classes="usage",
                        )
                        self._status.update(
                            f"累计上下文：{event.total_usage.context_tokens:,} tokens"
                        )
        except Exception as exc:
            await self._mount_markdown(f"### 错误\n\n`{exc}`")
        finally:
            self._prompt.disabled = False
            self._status.update("就绪")
            self._prompt.focus()

    @staticmethod
    def _format_usage(usage: ChatUsage, total: ChatUsage) -> str:
        return (
            "`本轮` "
            f"输入 {usage.input_tokens:,} · "
            f"输出 {usage.output_tokens:,} · "
            f"缓存 {usage.cache_tokens:,} "
            f"（读 {usage.cache_read_input_tokens:,} / "
            f"写 {usage.cache_creation_input_tokens:,}）· "
            f"上下文 {usage.context_tokens:,} tokens\n\n"
            "`累计` "
            f"输入 {total.input_tokens:,} · "
            f"输出 {total.output_tokens:,} · "
            f"缓存 {total.cache_tokens:,} · "
            f"上下文 {total.context_tokens:,} tokens"
        )
