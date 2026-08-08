"""内联单选组件：会话流里向用户提问并等一个选择。

Claude Code 版式的通用选择卡片——圆角框内四段：动作标题、可选的正文
预览、一句问句，再是 ``❯ 1. 选项`` 形式的编号列表。交互：↑/↓ 移光标、
数字键 1..9 直选、Enter 确认、Esc 命中 ``escape_index``（默认末项）。

选项可附带 ``placeholder`` —— 当光标移到该选项时，下方出现一个文本输入框，
用户可直接输入补充内容（如拒绝原因）。Enter 提交时输入文本随同选项值
一并回传（``ChoiceResult``），不输入直接 Enter 则文本为空字符串。

本模块只管"怎么问"，不认识任何业务概念（工具、审批、文件…）。使用方
把标题/正文/问句/选项拼好，经 ``ChatApp.ask_choice`` 挂出去即可。工具
审批是第一个使用方，见 ``tui/approval.py``。

注意：``ask_choice`` 必须在 worker 里 await（Textual 的按键派发走 App
消息泵，堵住泵卡片就收不到任何按键），详见 ``tui/app.py`` 的 ``_run_turn``。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Input, Static

from wy_coding_agent.tui.render import ACCENT, DIM


@dataclass(frozen=True, slots=True)
class Choice[T]:
    """一个待选项：展示给用户的 ``label`` + 选中后回传的 ``value``。

    若 ``placeholder`` 非空，光标移到该选项时下方会出现输入框供用户
    输入补充文本，Enter 提交时原样回传。
    """

    label: str
    value: T
    placeholder: str | None = None


@dataclass(frozen=True)
class ChoiceResult[T]:
    """``ask_choice`` 的返回值：选项值 + 附带输入的文本（无输入时为空）。"""

    value: T
    text: str = ""


class ChoiceWidget[T](Widget, can_focus=True):
    """内联在会话流中的单选卡片。

    选中结果经 ``asyncio.Future`` 回传 ``ChoiceResult(value, text)``，
    随后卡片自行从消息列表移除。
    """

    BINDINGS = [
        Binding("up", "cursor(-1)", "上一项", show=False),
        Binding("down", "cursor(1)", "下一项", show=False),
        Binding("enter", "select", "确认", show=False),
        Binding("escape", "escape", "取消", show=False),
        *[Binding(str(n), f"choose({n - 1})", "", show=False) for n in range(1, 10)],
    ]

    def __init__(
        self,
        future: asyncio.Future,
        *,
        heading: str = "",
        body: Text | str = "",
        question: str,
        choices: Sequence[Choice[T]],
        escape_index: int = -1,
    ) -> None:
        if not choices:
            raise ValueError("choices 不能为空")
        self._future = future
        self._heading = heading
        self._body = body
        self._question = question
        self._choices = tuple(choices)
        self._escape_index = escape_index % len(self._choices)
        self._cursor = 0
        # 找到第一个带 placeholder 的选项索引；只支持一个
        self._editable_index: int | None = None
        for idx, ch in enumerate(self._choices):
            if ch.placeholder is not None:
                self._editable_index = idx
                break
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="choice-card"):
            if self._heading:
                yield Static(self._heading, id="choice-heading")
            if self._body:
                yield Static(self._body, id="choice-body")
            yield Static(self._question, id="choice-question")
            with Vertical(id="choice-options"):
                for index in range(len(self._choices)):
                    yield Static(
                        id=f"choice-option-{index}", classes="choice-option"
                    )

    def on_mount(self) -> None:
        self._refresh_options()
        self.focus()

    # ── 命令动作 ─────────────────────────────────────────

    def action_cursor(self, delta: int) -> None:
        self._cursor = (self._cursor + delta) % len(self._choices)
        self._refresh_options()

    def action_select(self) -> None:
        """Enter：直接提交当前选项（可附带输入框文本）。"""
        text = self._read_input_text()
        self._resolve(self._cursor, text)

    def action_choose(self, index: int) -> None:
        """数字键直选。"""
        if 0 <= index < len(self._choices):
            text = self._read_input_text()
            self._resolve(index, text)

    def action_escape(self) -> None:
        """Esc：选中 escape 选项，不附带任何输入文本。"""
        if not self._future.done():
            self._future.set_result(
                ChoiceResult(value=self._choices[self._escape_index].value, text="")
            )
        self.remove()

    # ── 输入框交互 ───────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """输入框中按 Enter → 提交当前选项 + 输入文本。"""
        event.stop()
        self._resolve(self._cursor, event.value)

    def _read_input_text(self) -> str:
        """读取当前输入框内容（无输入框时返回空字符串）。"""
        try:
            inp = self.query_one("#choice-input", Input)
            return inp.value
        except Exception:
            return ""

    # ── 内部 ─────────────────────────────────────────────

    def _refresh_options(self) -> None:
        for index, choice in enumerate(self._choices):
            selected = index == self._cursor
            row = Text()
            row.append("❯ " if selected else "  ", style=ACCENT)
            row.append(
                f"{index + 1}. {choice.label}",
                style=ACCENT if selected else DIM,
            )
            self.query_one(f"#choice-option-{index}", Static).update(row)

        self._sync_editable_input()

    def _sync_editable_input(self) -> None:
        """光标在可编辑选项上时显示输入框并聚焦，否则移除并聚焦回卡片。"""
        if self._editable_index is not None and self._cursor == self._editable_index:
            if not self.query("#choice-input"):
                placeholder = self._choices[self._editable_index].placeholder or ""
                card = self.query_one("#choice-card")
                card.mount(
                    Input(placeholder=placeholder, id="choice-input"),
                    after="#choice-options",
                )
                self.query_one("#choice-input", Input).focus()
        else:
            removed = self.query("#choice-input")
            if removed:
                removed.remove()
                self.focus()

    def _resolve(self, index: int, text: str) -> None:
        if not self._future.done():
            self._future.set_result(
                ChoiceResult(value=self._choices[index].value, text=text)
            )
        self.remove()
