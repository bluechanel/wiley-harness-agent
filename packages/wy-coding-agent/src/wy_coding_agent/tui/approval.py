"""工具审批 TUI：内联审批卡片 + 审批交互桥接。

``ApprovalWidget`` 是内联在会话流中的审批卡片,参考 Claude Code 审批样式:
工具名醒目、参数摘要、[A]批准/[R]拒绝 快捷键。

``TuiApprovalHandler`` 实现 ``ApprovalHandler`` 协议,
将审批请求桥接到内联 Widget——在 Agent 的 async 流内挂载卡片,
通过 ``asyncio.Future`` 阻塞等待用户决定。不使用 ModalScreen,
审批卡片直接出现在消息列表的工具调用与工具输出之间。
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static
from textual.widget import Widget

from wy_core import ToolApproval, ToolCall

from wy_coding_agent.tool_policy import ApprovalHandler

if TYPE_CHECKING:
    from wy_coding_agent.tui.app import ChatApp

_MAX_PARAM_CHARS = 500

# ── 参数智能展示 ────────────────────────────────────────────

_BASH_KEYS = ("command",)
_FILE_KEYS = ("file_path", "old_string", "new_string", "content", "path")


def _smart_params(call: ToolCall) -> list[tuple[str, str]]:
    """从 ToolCall.input 提取可读的参数摘要。"""
    pairs: list[tuple[str, str]] = []
    if call.name == "bash":
        for k in _BASH_KEYS:
            if k in call.input:
                val = str(call.input[k])
                pairs.append(("命令", val[:300]))
    elif call.name in ("read", "write", "edit"):
        for k in _FILE_KEYS:
            if k in call.input:
                val = str(call.input[k])
                ellipsis = "…" if len(val) > 120 else ""
                pairs.append(("文件" if k == "file_path" else k, val[:120] + ellipsis))
    if not pairs:
        raw = json.dumps(call.input, ensure_ascii=False, indent=2)
        if len(raw) > _MAX_PARAM_CHARS:
            raw = raw[:_MAX_PARAM_CHARS] + "\n…"
        pairs.append(("参数", raw))
    return pairs


# ── 内联审批卡片 ─────────────────────────────────────────────


class ApprovalWidget(Widget, can_focus=True):
    """内联在会话流中的工具审批卡片。

    提供按钮点击与键盘快捷键两种交互方式：
    - a / Enter → 批准
    - r / Esc → 拒绝

    通过 ``asyncio.Future`` 将用户决定传回 `TuiApprovalHandler`。
    """

    BINDINGS = [
        Binding("a", "approve", "批准", show=False),
        Binding("r", "reject", "拒绝", show=False),
    ]

    def __init__(self, call: ToolCall, future: asyncio.Future) -> None:
        self._call = call
        self._future = future
        super().__init__()

    def compose(self) -> ComposeResult:
        param_pairs = _smart_params(self._call)
        with Vertical(id="approval-card"):
            yield Static("🔧 工具审批", id="approval-title")
            yield Static(f"工具\n  {self._call.name}", id="approval-tool")
            for label, value in param_pairs:
                yield Static(f"{label}\n  {value}", id="approval-param")
            yield Static("", id="approval-sep")
            with Horizontal(id="approval-actions"):
                yield Button("A. 批准", id="approval-accept")
                yield Button("R. 拒绝", id="approval-deny")

    def on_mount(self) -> None:
        self.focus()

    # ── 命令动作 ─────────────────────────────────────────

    def action_approve(self) -> None:
        self._resolve(ToolApproval(allowed=True, reason="用户批准"))

    def action_reject(self) -> None:
        self._resolve(ToolApproval(allowed=False, reason="用户拒绝"))

    # ── 按钮回调 ─────────────────────────────────────────

    @on(Button.Pressed, "#approval-accept")
    def _on_accept(self) -> None:
        self.action_approve()

    @on(Button.Pressed, "#approval-deny")
    def _on_deny(self) -> None:
        self.action_reject()

    # ── 内部 ─────────────────────────────────────────────

    def _resolve(self, decision: ToolApproval) -> None:
        if not self._future.done():
            self._future.set_result(decision)
        self.remove()


# ── 审批交互桥接 ───────────────────────────────────────────


class TuiApprovalHandler(ApprovalHandler):
    """把审批请求桥接到内联 ``ApprovalWidget``。

    在 ``ChatApp.on_mount`` 中创建并注入到 ``WorkspaceToolHook``:
    当 Agent 执行到需要审批的工具时,hook 调用 ``request_approval``,
    本实现在消息列表尾部挂载 ``ApprovalWidget`` 卡片,
    通过 ``asyncio.Future`` 阻塞等待用户决定。
    """

    def __init__(self, app: ChatApp) -> None:
        self._app = app

    async def request_approval(self, call: ToolCall) -> ToolApproval:
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        widget = ApprovalWidget(call, future)
        await self._app._message_list.mount(widget)
        self._app._scroll_to_bottom()
        try:
            return await future
        except Exception:
            return ToolApproval(allowed=False, reason="审批交互异常")
