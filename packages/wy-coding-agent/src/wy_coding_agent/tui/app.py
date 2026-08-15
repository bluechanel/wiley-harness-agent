"""Textual chat application styled after Claude Code's terminal UI.

界面仿照 Claude Code:无 Header/Footer 的滚动会话区(启动横幅 + 沟槽
符号行),回合进行中在输入框上方显示动画活动行,底部是圆角输入框与
提示/用量行。本层只做界面组件与交互;记录/事件到视图的解析在 render。
"""

import asyncio
import time
from collections.abc import Sequence
from typing import AsyncIterator, Protocol

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Collapsible, Input, Markdown, Static

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

from wy_coding_agent.reminders import HarnessState
from wy_coding_agent.session import SessionRecord
from wy_coding_agent.tui.approval import TuiApprovalHandler
from wy_coding_agent.tui.choice import Choice, ChoiceResult, ChoiceWidget
import wy_coding_agent.tui.render as render


class ChatBackend(Protocol):
    plan_mode: HarnessState | None

    def stream(self, user_input: str) -> AsyncIterator[AgentEvent]: ...

    def set_plan_mode(self, active: bool) -> None: ...

    def save_state(self) -> None: ...


class ChatApp(App[None]):
    """Claude Code 风格的聊天界面。"""

    # 调色板与 render.py 的常量保持一致(CSS 无法引用 Python 常量)。
    CSS = """
    Screen {
        background: $surface;
    }

    #conversation {
        height: 1fr;
        padding: 1 2 0 2;
        scrollbar-size-vertical: 1;
    }

    #message-list {
        min-height: 100%;
        height: auto;
        align: left bottom;
    }

    #message-list > .banner {
        border: round #D77757;
        width: auto;
        padding: 0 2;
        margin: 0 0 1 0;
    }

    #message-list > .line {
        margin: 0 0 1 0;
        color: #8A8A8A;
    }

    #message-list > Markdown {
        margin: 0 0 1 0;
        padding: 0;
        background: transparent;
    }

    #message-list > .plan {
        border: round #D77757;
        border-title-color: #D77757;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    #message-list > .row {
        height: auto;
        margin: 0 0 1 0;
    }

    #message-list > .row > .gutter {
        width: 2;
        color: $text;
    }

    #message-list > .row > .body {
        width: 1fr;
    }

    #message-list > .row > Markdown {
        width: 1fr;
        margin: 0;
        padding: 0;
        background: transparent;
    }

    #message-list > .user > .gutter {
        color: #8A8A8A;
    }

    #message-list > .user > .body {
        color: #B0B0B0;
    }

    #message-list > .error > .gutter,
    #message-list > .error > .body {
        color: #E5484D;
    }

    /* Claude Code 风格折叠块:去掉 Collapsible 自带的分隔线与底色,
       只留"符号 + 标题"一行;正文缩进到符号沟槽之后。 */
    #message-list Collapsible {
        background: transparent;
        border: none;
        padding: 0;
        margin: 0 0 1 0;
    }

    #message-list Collapsible > CollapsibleTitle {
        padding: 0;
        background: transparent;
        color: $text;
        text-style: none;
    }

    #message-list Collapsible > CollapsibleTitle:hover {
        background: $panel;
        color: $text;
    }

    #message-list Collapsible > CollapsibleTitle:focus {
        background: $panel;
        color: $text;
        text-style: none;
    }

    #message-list Collapsible Contents {
        padding: 0 0 0 2;
    }

    #message-list Collapsible Markdown {
        margin: 0 0 1 0;
        padding: 0;
        background: transparent;
    }

    #message-list > .thinking > CollapsibleTitle {
        color: #8A8A8A;
        text-style: italic;
    }

    #message-list > .thinking Markdown {
        color: #9A9A9A;
    }

    /* 工具调用与其输出贴紧成组,组后留一行空隙。 */
    #message-list > .tool-call {
        margin: 0;
    }

    #message-list > .tool-call > CollapsibleTitle {
        color: #4EBF71;
    }

    #message-list > .tool-result {
        padding: 0 0 0 2;
    }

    #message-list > .tool-result > CollapsibleTitle {
        color: #8A8A8A;
    }

    #message-list > .tool-result.error > CollapsibleTitle {
        color: #E5484D;
    }

    #message-list > .compaction > CollapsibleTitle {
        color: #8A8A8A;
    }

    #message-list Collapsible Markdown,
    #message-list > .tool-result Markdown,
    #message-list > .tool-call Markdown {
        color: #9A9A9A;
    }

    #activity {
        height: 1;
        margin: 1 2 0 2;
        display: none;
    }

    #activity.running {
        display: block;
    }

    #input-area {
        height: 3;
        margin: 1 2 0 2;
        padding: 0 1;
        border: round #565656;
    }

    #input-area:focus-within {
        border: round #D77757;
    }

    #input-prompt {
        width: 2;
        color: $text;
        text-style: bold;
    }

    #prompt {
        width: 1fr;
        height: 1;
        border: none;
        padding: 0;
        background: transparent;
    }

    #prompt:focus {
        border: none;
        background: transparent;
        background-tint: $foreground 0%;
    }

    #status-bar {
        height: 1;
        margin: 0 3;
    }

    #status {
        width: 1fr;
    }

    #usage {
        width: auto;
        color: #8A8A8A;
    }

    /* ── 内联单选卡片(Claude Code 风格) ────────────────
       圆角橙框内:标题 / 缩进正文预览 / 问句 / 编号选项列表。
       通用组件,工具审批只是其使用方之一,故用 choice- 前缀。 */

    #message-list > ChoiceWidget {
        width: 1fr;
        height: auto;
        border: round #D77757;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    #message-list > ChoiceWidget:focus {
        border: round #D77757;
    }

    #choice-card {
        width: 1fr;
        height: auto;
        padding: 0;
    }

    #choice-heading {
        width: 1fr;
        color: #DEDEDE;
        text-style: bold;
    }

    #choice-body {
        width: 1fr;
        padding: 1 0;
    }

    #choice-question {
        width: 1fr;
        color: #DEDEDE;
    }

    #choice-options {
        width: 1fr;
        height: auto;
    }

    #choice-options > .choice-option {
        width: 1fr;
    }

    #choice-input {
        width: 1fr;
        margin: 0;
    }
    """

    TITLE = "Wy Coding Agent"
    BINDINGS = [
        Binding("ctrl+d", "quit", "退出", show=False),
    ]

    def __init__(
        self,
        chat: ChatBackend,
        *,
        session_id: str = "",
        history: tuple[SessionRecord, ...] = (),
        total_usage: Usage | None = None,
        context_tokens: int = 0,
        model_name: str = "",
        workspace: str = "",
        context_limit: int = 0,
    ) -> None:
        super().__init__()
        self.chat = chat
        self.session_id = session_id
        self.history = history
        self.total_usage = total_usage if total_usage is not None else Usage()
        self.context_tokens = context_tokens
        self.model_name = model_name
        self.workspace = workspace
        self.context_limit = context_limit
        self._spinner_tick = 0
        self._turn_started = 0.0
        self._activity_verb = ""

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="conversation"):
            yield Vertical(id="message-list")
        yield Static("", id="activity")
        with Horizontal(id="input-area"):
            yield Static("> ", id="input-prompt")
            yield Input(placeholder="想让我做什么?", id="prompt")
        with Horizontal(id="status-bar"):
            yield Static("", id="status")
            yield Static(
                render.usage_bar_text(
                    self.total_usage, self.context_tokens, self.context_limit
                ),
                id="usage",
            )

    def on_mount(self) -> None:
        self._prompt.focus()
        self._refresh_hints()
        self._spinner_timer = self.set_interval(
            0.125, self._tick_spinner, pause=True
        )
        # 将 TUI 审批交互注入到工具审批钩子（延迟绑定：Agent 先于 TUI 创建）
        hook = self.chat.tool_hook
        if hook is not None and hasattr(hook, "set_handler"):
            hook.set_handler(TuiApprovalHandler(self))
        self.call_after_refresh(self._render_history)

    async def _render_history(self) -> None:
        await self._message_list.mount(
            Static(
                render.banner_text(self.model_name, self.workspace, self.session_id),
                classes="banner",
            )
        )
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
    def _activity(self) -> Static:
        return self.query_one("#activity", Static)

    @property
    def _message_list(self) -> Vertical:
        return self.query_one("#message-list", Vertical)

    async def _mount_view(self, view: render.MessageView) -> Markdown | None:
        """Mount one view. Returns the inner Markdown widget when the block
        body is Markdown, so streamed deltas can keep updating it."""
        if view.collapsible_title:
            body = Markdown(view.markdown)
            await self._message_list.mount(
                Collapsible(
                    body,
                    title=view.collapsible_title,
                    collapsed=True,
                    collapsed_symbol=view.symbol or "▶",
                    expanded_symbol=view.symbol or "▼",
                    classes=view.classes,
                )
            )
            self._scroll_to_bottom()
            return body
        if view.text:
            # 原文块:经 rich.Text 挂载,杜绝用户/错误文本被当 markup 解析。
            if view.symbol:
                widget = Horizontal(
                    Static(view.symbol, classes="gutter"),
                    Static(Text(view.text), classes="body"),
                    classes=f"row {view.classes}",
                )
            else:
                widget = Static(Text(view.text), classes=f"line {view.classes}")
            await self._message_list.mount(widget)
            self._scroll_to_bottom()
            return None
        body = Markdown(view.markdown, classes="" if view.symbol else view.classes)
        if view.symbol:
            await self._message_list.mount(
                Horizontal(
                    Static(view.symbol, classes="gutter"),
                    body,
                    classes=f"row {view.classes}",
                )
            )
        else:
            if "plan" in view.classes.split():
                body.border_title = "计划"
            await self._message_list.mount(body)
        self._scroll_to_bottom()
        return body

    async def _update_markdown(self, widget: Markdown, content: str) -> None:
        await widget.update(content)
        self._scroll_to_bottom()

    async def ask_choice[T](
        self,
        *,
        question: str,
        choices: Sequence[Choice[T]],
        heading: str = "",
        body: Text | str = "",
        escape_index: int = -1,
    ) -> ChoiceResult[T]:
        """在会话流里挂一张单选卡片，等用户选完并返回 ``ChoiceResult``。

        任何"要用户当场选一下"的交互都走这里（工具审批是第一个使用方）。
        必须在 worker 里 await——直接在消息处理器里等会堵死 App 消息泵，
        卡片挂得出来却收不到按键，见 ``_run_turn``。
        """
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._message_list.mount(
            ChoiceWidget(
                future,
                heading=heading,
                body=body,
                question=question,
                choices=choices,
                escape_index=escape_index,
            )
        )
        self._scroll_to_bottom()
        return await future

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

        await self._mount_view(render.user_view(user_input))
        self._prompt.disabled = True
        self._begin_turn()
        # 回合必须跑在 worker 里：审批卡片等交互要在回合**进行中**收键盘
        # 事件，而事件派发走 App 的消息泵——直接在处理器里 await 整个
        # 回合会把泵堵死，卡片挂得出来却按不动。
        self._run_turn(user_input)

    @work
    async def _run_turn(self, user_input: str) -> None:
        """跑完一个回合：消费事件流并落到界面。由 worker 驱动，不占消息泵。"""
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
                    self._set_verb("思考中")
                    await self._update_markdown(reasoning_widget, reasoning_text)
                elif isinstance(chunk, TextDelta):
                    if answer_widget is None:
                        answer_widget = await self._mount_view(
                            render.answer_view("")
                        )
                    answer_text += chunk.text
                    self._set_verb("撰写回复")
                    await self._update_markdown(answer_widget, answer_text)
                elif isinstance(chunk, ToolCall):
                    # 后续轮次的思考/回答另起新块，保持与工具块的时间顺序。
                    reasoning_widget = None
                    answer_widget = None
                    reasoning_text = ""
                    answer_text = ""
                    self._set_verb(f"运行 {chunk.name}")
                    await self._mount_view(
                        render.tool_call_view(chunk.name, chunk.input)
                    )
                elif isinstance(chunk, ToolResult):
                    self._set_verb("请求中")
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
                    self._set_verb("压缩上下文")
                    await self._mount_view(
                        render.compaction_view(chunk.dropped, chunk.summary)
                    )
                elif isinstance(chunk, TurnEnd):
                    self._usage.update(
                        render.usage_bar_text(
                            chunk.usage, chunk.context_tokens, self.context_limit
                        )
                    )
        except Exception as exc:
            await self._mount_view(render.error_view(exc))
        finally:
            self._end_turn()
            self._prompt.disabled = False
            self._prompt.focus()

    async def _enter_plan_mode(self, command: str) -> str:
        """处理 /plan 本地命令(纯 harness 状态切换,不发给模型);
        返回需要继续作为用户输入发送的剩余参数,可为空。"""
        plan_mode = getattr(self.chat, "plan_mode", None)
        if plan_mode is None:
            await self._mount_view(render.error_view("当前后端不支持 plan 模式"))
            return ""
        self.chat.set_plan_mode(True)  # 翻转 harness 状态并即时落盘;system 由提交时组装
        await self._mount_view(render.PLAN_MODE_VIEW)
        self._refresh_hints()
        return command[len("/plan"):].strip()

    def _begin_turn(self) -> None:
        self._turn_started = time.monotonic()
        self._activity_verb = "请求中"
        self._activity.add_class("running")
        self._refresh_activity()
        self._spinner_timer.resume()

    def _end_turn(self) -> None:
        self._spinner_timer.pause()
        self._activity.remove_class("running")
        self._activity.update("")
        self._refresh_hints()

    def _set_verb(self, verb: str) -> None:
        if verb != self._activity_verb:
            self._activity_verb = verb
            self._refresh_activity()

    def _tick_spinner(self) -> None:
        self._spinner_tick += 1
        self._refresh_activity()

    def _refresh_activity(self) -> None:
        self._activity.update(
            render.spinner_text(
                self._spinner_tick,
                self._activity_verb,
                time.monotonic() - self._turn_started,
            )
        )

    def _refresh_hints(self) -> None:
        plan_mode = getattr(self.chat, "plan_mode", None)
        self._status.update(
            render.hint_text(plan_mode is not None and plan_mode.plan_active)
        )
