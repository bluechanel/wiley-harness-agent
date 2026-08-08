"""工具审批：把一次工具调用翻译成一张选择卡片，再把选择翻译回裁决。

审批的"怎么问"全部交给通用组件 ``tui/choice.py``（内联单选卡片）；
本模块只负责工具域的知识——从 ``ApprovalRequest`` 读取展示信息并渲染。

拒绝选项附带 ``placeholder``，光标移到该选项时下方出现输入框，
用户可直接输入拒绝原因；Enter 提交时原因随选项一并回传。

``TuiApprovalHandler`` 实现 ``ApprovalHandler`` 协议：调
``ChatApp.ask_choice`` 挂卡片、拿到 ``ChoiceResult`` 后转成
``ToolApproval``。"不再询问"选项在 ``ApprovalRequest.key`` 非空时出现；
展示层不硬依赖具体 hook 类型。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.text import Text

from wy_core import ApprovalRequest, ToolApproval, ToolCall

from wy_coding_agent.tool_policy import ApprovalHandler
from wy_coding_agent.tui.choice import Choice
from wy_coding_agent.tui.render import DIM, RED, TEXT

if TYPE_CHECKING:
    from wy_coding_agent.tui.app import ChatApp

ADDED = "#4EBF71"  # 新增行，与 render.py 的 .tool-call 绿一致

_MAX_PREVIEW_LINES = 12
_MAX_LINE_CHARS = 160


# ── 卡片渲染 ────────────────────────────────────────────────


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


def render_fields(fields: list[tuple[str, str]]) -> Text:
    """通用字段渲染：标签加粗，值按约定着色。

    "删除" 标签用红色（diff 删行），"新增" 标签用绿色（diff 加行），
    其余标签用默认正文色。
    """
    body = Text()
    for label, value in fields:
        style = RED if label == "删除" else ADDED if label == "新增" else TEXT
        body.append(f"{label}：", style=DIM)
        for line in _clip(value):
            body.append(f"  {line}\n", style=style)
    body.rstrip()
    return body


# ── 选项 ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ApprovalOption:
    """一个审批选项的语义：裁决 + 是否要 hook 记住这次选择。"""

    allowed: bool
    reason: str
    remember: bool = False


def build_choices(request: ApprovalRequest) -> list[Choice[ApprovalOption]]:
    """构造选项列表：是 /（可选）不再询问 / 否。

    末项恒为拒绝（Esc 命中），附带输入框供用户直接输入拒绝原因。
    """
    choices: list[Choice[ApprovalOption]] = [
        Choice("是", ApprovalOption(allowed=True, reason="用户批准")),
    ]
    if request.key is not None:
        choices.append(
            Choice(
                "是，且本次会话不再询问",
                ApprovalOption(
                    allowed=True,
                    reason="用户批准（本次会话不再询问）",
                    remember=True,
                ),
            )
        )
    choices.append(
        Choice(
            "否 (esc)",
            ApprovalOption(allowed=False, reason="用户拒绝"),
            placeholder="拒绝，并告诉模型该怎么做...",
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

    async def request_approval(
        self, call: ToolCall, request: ApprovalRequest
    ) -> ToolApproval:
        remember = self._remember_callback(call)
        try:
            result = await self._app.ask_choice(
                heading=request.heading,
                body=render_fields(request.fields),
                question=request.question,
                choices=build_choices(request),
            )
        except Exception:
            return ToolApproval(allowed=False, reason="审批交互异常")

        option: ApprovalOption = result.value

        if option.allowed:
            if option.remember and remember is not None:
                remember(call)
            return ToolApproval(allowed=True, reason=option.reason)

        # 用户拒绝 —— 附带原因输入（如果有）
        reason = result.text.strip()
        if reason:
            return ToolApproval(
                allowed=False,
                reason=f"用户拒绝了工具执行，原因为{reason}",
            )
        return ToolApproval(allowed=False, reason="用户拒绝")

    def _remember_callback(self, call: ToolCall) -> Callable[[ToolCall], None] | None:
        """hook 支持记住该调用时返回回调，否则 None（不显示该选项）。"""
        hook = getattr(self._app.chat, "tool_hook", None)
        allow_always = getattr(hook, "allow_always", None)
        can_remember = getattr(hook, "can_remember", None)
        if not callable(allow_always) or not callable(can_remember):
            return None
        return allow_always if can_remember(call) else None
