"""工具审批：把一次工具调用翻译成一张选择卡片，再把选择翻译回裁决。

审批的"怎么问"全部交给通用组件 ``tui/choice.py``（内联单选卡片）；
本模块只负责工具域的知识——动作标题（"Bash 命令"/"编辑文件"…）、
入参预览（edit 走 ``-``/``+`` 差异着色）、确认问句与三个选项的措辞。

``TuiApprovalHandler`` 实现 ``ApprovalHandler`` 协议：调
``ChatApp.ask_choice`` 挂卡片、拿到 ``ApprovalOption`` 后转成
``ToolApproval``。"不再询问"选项仅在底层 hook 支持时出现（鸭子类型探测
``can_remember``/``allow_always``），展示层不硬依赖具体 hook 类型。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.text import Text

from wy_core import ToolApproval, ToolCall

from wy_coding_agent.tool_policy import ApprovalHandler
from wy_coding_agent.tui.choice import Choice
from wy_coding_agent.tui.render import DIM, RED, TEXT

if TYPE_CHECKING:
    from wy_coding_agent.tui.app import ChatApp

ADDED = "#4EBF71"  # 新增行，与 render.py 的 .tool-call 绿一致

_MAX_PREVIEW_LINES = 12
_MAX_LINE_CHARS = 160
_PATH_TOOLS = ("read", "write", "edit")
_PATH_KEYS = ("file_path", "path")

# 动作标题与确认问句：按工具名给出人话，未知工具回落到通用文案。
_HEADINGS = {
    "bash": "Bash 命令",
    "read": "读取文件",
    "write": "写入文件",
    "edit": "编辑文件",
}
_QUESTIONS = {
    "bash": "是否执行该命令？",
    "read": "是否读取该文件？",
    "write": "是否写入该文件？",
    "edit": "是否应用该编辑？",
}
_REMEMBER_LABELS = {
    "bash": "是，且本次会话不再询问该命令",
    "read": "是，且本次会话不再询问该文件",
    "write": "是，且本次会话不再询问该文件",
    "edit": "是，且本次会话不再询问该文件",
}


# ── 卡片文案 ────────────────────────────────────────────────


def heading(call: ToolCall) -> str:
    return _HEADINGS.get(call.name, f"{call.name} 工具调用")


def question(call: ToolCall) -> str:
    return _QUESTIONS.get(call.name, "是否继续？")


def _clip(raw: str) -> list[str]:
    """按行截断预览：单行超长截尾，超出行数折叠成一行提示。"""
    lines = raw.splitlines() or ([raw] if raw else [])
    clipped = [
        line[:_MAX_LINE_CHARS] + ("…" if len(line) > _MAX_LINE_CHARS else "")
        for line in lines[:_MAX_PREVIEW_LINES]
    ]
    if len(lines) > _MAX_PREVIEW_LINES:
        clipped.append(f"… 另有 {len(lines) - _MAX_PREVIEW_LINES} 行")
    return clipped


def preview_text(call: ToolCall) -> Text:
    """把工具入参渲染成卡片正文：统一缩进两格，edit 走 -/+ 差异着色。"""
    body = Text()

    def emit(lines: list[str], style: str, prefix: str = "") -> None:
        for line in lines:
            body.append(f"  {prefix}{line}\n", style=style)

    def done() -> Text:
        body.rstrip()  # rich 原地裁剪尾部空行，返回值为 None
        return body

    if call.name == "bash":
        emit(_clip(str(call.input.get("command", ""))), TEXT)
        description = str(call.input.get("description", "")).strip()
        if description:
            emit(_clip(description), DIM)
        return done()

    if call.name in _PATH_TOOLS:
        path = next((str(call.input[k]) for k in _PATH_KEYS if call.input.get(k)), "")
        if path:
            emit([path], TEXT)
        old = str(call.input.get("old_string", ""))
        new = str(call.input.get("new_string", ""))
        content = str(call.input.get("content", ""))
        if old or new:
            body.append("\n")
            emit(_clip(old), RED, prefix="- ")
            emit(_clip(new), ADDED, prefix="+ ")
        elif content:
            body.append("\n")
            emit(_clip(content), DIM)
        return done()

    raw = (
        json.dumps(call.input, ensure_ascii=False, indent=2)
        if call.input
        else "（无参数）"
    )
    emit(_clip(raw), DIM)
    return done()


# ── 选项 ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ApprovalOption:
    """一个审批选项的语义：裁决 + 是否要 hook 记住这次选择。"""

    allowed: bool
    reason: str
    remember: bool = False


def build_choices(
    call: ToolCall, *, can_remember: bool
) -> list[Choice[ApprovalOption]]:
    """构造选项列表：是 /（可选）不再询问 / 否，末项恒为拒绝（Esc 命中）。"""
    choices = [
        Choice("是", ApprovalOption(allowed=True, reason="用户批准")),
    ]
    if can_remember:
        choices.append(
            Choice(
                _REMEMBER_LABELS.get(call.name, "是，且本次会话不再询问"),
                ApprovalOption(
                    allowed=True,
                    reason="用户批准（本次会话不再询问）",
                    remember=True,
                ),
            )
        )
    choices.append(
        Choice(
            "否，并告诉我该怎么做 (esc)",
            ApprovalOption(allowed=False, reason="用户拒绝"),
        )
    )
    return choices


# ── 审批交互桥接 ───────────────────────────────────────────


class TuiApprovalHandler(ApprovalHandler):
    """把审批请求翻译成一次 ``ChatApp.ask_choice``。

    在 ``ChatApp.on_mount`` 中创建并注入到 ``WorkspaceToolHook``：
    当 Agent 执行到需要审批的工具时，hook 调用 ``request_approval``，
    本实现挂出选择卡片并阻塞等待用户决定。
    """

    def __init__(self, app: ChatApp) -> None:
        self._app = app

    async def request_approval(self, call: ToolCall) -> ToolApproval:
        remember = self._remember_callback(call)
        try:
            option = await self._app.ask_choice(
                heading=heading(call),
                body=preview_text(call),
                question=question(call),
                choices=build_choices(call, can_remember=remember is not None),
            )
        except Exception:
            return ToolApproval(allowed=False, reason="审批交互异常")
        if option.remember and remember is not None:
            remember(call)
        return ToolApproval(allowed=option.allowed, reason=option.reason)

    def _remember_callback(self, call: ToolCall) -> Callable[[ToolCall], None] | None:
        """hook 支持记住该调用时返回回调，否则 None（不显示该选项）。"""
        hook = getattr(self._app.chat, "tool_hook", None)
        allow_always = getattr(hook, "allow_always", None)
        can_remember = getattr(hook, "can_remember", None)
        if not callable(allow_always) or not callable(can_remember):
            return None
        return allow_always if can_remember(call) else None
