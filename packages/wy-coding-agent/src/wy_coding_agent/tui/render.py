"""Parse agent-layer records and usage into display views for the TUI.

视图词汇仿照 Claude Code 的终端样式:
- 用户输入:``>`` 沟槽 + 原文(不渲染 Markdown、不解析标记);
- 助手回复:``⏺`` 沟槽 + Markdown 正文;
- 工具调用:``⏺ name(入参摘要)`` 一行,完整入参默认收起;
- 工具输出:``⎿ 首行预览 (+N 行)`` 一行,完整输出默认收起;
- 思考 ``✻`` / 上下文压缩 ``✽``:弱化折叠块;
- 计划(exit_plan_mode):橙色圆角边框内展开的 Markdown。

本模块保持纯函数:只产出 ``MessageView`` 数据与 markup 字符串,
不 import Textual;动态内容进 markup 前一律 ``escape`` 转义。
"""

import json
import re
from dataclasses import dataclass

from rich.markup import escape

from wy_core import Usage

from wy_coding_agent.session import SessionRecord

# Claude Code 风格调色板(app.py 的 CSS 与此保持一致)。
ACCENT = "#D77757"  # 品牌橙:欢迎符、spinner、plan 标识、计划边框
DIM = "#8A8A8A"  # 弱化文本:提示、摘要附注、思考/压缩块
TEXT = "#DEDEDE"  # 折叠标题中需要盖过 CSS 底色的常规文本
RED = "#E5484D"  # 错误

SPINNER_FRAMES = ("·", "✢", "*", "✻", "✽", "✻", "*", "✢")

_TOOL_PAYLOAD_MAX_LINES = 30
_TOOL_PAYLOAD_MAX_CHARS = 2000
_SUMMARY_MAX_CHARS = 64
# 工具调用一行摘要的取值优先级:命中首个非空字符串字段即用作摘要。
_SUMMARY_KEYS = (
    "command",
    "file_path",
    "path",
    "pattern",
    "query",
    "skill",
    "description",
    "task",
    "plan",
)


@dataclass(frozen=True, slots=True)
class MessageView:
    """One renderable conversation block plus presentation hints.

    三种形态,由字段组合决定:
    - ``collapsible_title`` 非空:默认收起的折叠块,标题为 content
      markup,``symbol`` 为折叠符(⏺/⎿/✻/✽),``markdown`` 为展开正文;
    - ``text`` 非空:原文块(不经 Markdown/markup 解析),``symbol``
      非空时带沟槽符;
    - 其余:Markdown 块,``symbol`` 非空时带沟槽符(助手回复)。
    """

    markdown: str = ""
    text: str = ""
    classes: str = ""
    collapsible_title: str = ""
    symbol: str = ""


PLAN_MODE_VIEW = MessageView(
    text="⏸ 已进入 plan 模式:助手将只调研与设计,经 exit_plan_mode 提交计划后自动退出。",
    classes="system",
)


def user_view(content: str) -> MessageView:
    return MessageView(text=content, classes="user", symbol=">")


def answer_view(markdown: str) -> MessageView:
    return MessageView(markdown=markdown, classes="assistant", symbol="⏺")


def error_view(error: object) -> MessageView:
    return MessageView(text=str(error), classes="error", symbol="⏺")


def reasoning_view(text: str) -> MessageView:
    return MessageView(
        markdown=text,
        classes="thinking",
        collapsible_title="思考过程",
        symbol="✻",
    )


def tool_call_view(tool_name: str, arguments: object) -> MessageView:
    if tool_name == "exit_plan_mode" and isinstance(arguments, dict):
        plan = str(arguments.get("plan", ""))
        if plan.strip():
            # 计划是给用户审阅的正文,直接以 Markdown 展开,不折叠不围栏。
            return MessageView(markdown=plan, classes="plan")
    # 圆点颜色来自 CSS(绿);名称与摘要用 markup 盖回常规/弱化色。
    title = (
        f"[{TEXT} b]{escape(tool_name)}[/]"
        f"[{DIM}]({escape(_call_summary(arguments))})[/]"
    )
    return MessageView(
        markdown=_payload_markdown(arguments),
        classes="tool-call",
        collapsible_title=title,
        symbol="⏺",
    )


def tool_output_view(
    tool_name: str, output: object, *, is_error: bool = False
) -> MessageView:
    preview, extra = _preview(output)
    title = escape(preview + (f" ({extra})" if extra else ""))
    return MessageView(
        markdown=_payload_markdown(output),
        classes="tool-result error" if is_error else "tool-result",
        collapsible_title=title,
        symbol="⎿",
    )


def compaction_view(dropped: int, summary: object) -> MessageView:
    return MessageView(
        markdown=_payload_markdown(summary),
        classes="compaction",
        collapsible_title=f"上下文已压缩 · 总结了 {dropped} 条早前消息",
        symbol="✽",
    )


def banner_text(model_name: str, workspace: str, session_id: str) -> str:
    """启动横幅 markup:欢迎行 + 模型/目录/会话详情(缺省项跳过)。"""
    lines = [f"[{ACCENT}]✻[/] 欢迎使用 [b]Wy Coding Agent[/b]!"]
    details = [("模型", model_name), ("目录", workspace), ("会话", session_id)]
    rows = [
        f"[{DIM}]{label}[/]  {escape(value)}" for label, value in details if value
    ]
    if rows:
        lines.append("")
        lines.extend(rows)
    return "\n".join(lines)


def hint_text(plan_active: bool) -> str:
    """输入框下方的常驻提示行 markup;plan 模式给出橙色标识。"""
    if plan_active:
        return (
            f"[{ACCENT}]⏸ plan 模式[/]"
            f"[{DIM}] · exit_plan_mode 提交计划后退出 · exit 退出[/]"
        )
    return f"[{DIM}]Enter 发送 · /plan 计划模式 · exit 退出[/]"


def spinner_text(tick: int, verb: str, seconds: float) -> str:
    """回合进行中的活动行 markup:动画符 + 动词 + 已耗时。"""
    frame = SPINNER_FRAMES[tick % len(SPINNER_FRAMES)]
    return f"[{ACCENT}]{frame}[/] {verb}… [{DIM}]({int(seconds)}s)[/]"


def usage_bar_text(total: Usage, context_tokens: int, context_limit: int = 0) -> str:
    """One-line usage summary: accumulated totals plus the live context size.

    context_limit(自动压缩阈值)非 0 时附"距自动压缩"余量百分比。
    """
    cache_tokens = total.cache_read_tokens + total.cache_write_tokens
    parts = [
        f"输入 {_compact_number(total.input_tokens)}",
        f"输出 {_compact_number(total.output_tokens)}",
        f"缓存 {_compact_number(cache_tokens)}",
        f"上下文 {_compact_number(context_tokens)}",
    ]
    if context_limit > 0:
        left = max(0, context_limit - context_tokens)
        parts.append(f"距自动压缩 {left * 100 // context_limit}%")
    return " · ".join(parts)


def render_record(record: SessionRecord) -> list[MessageView]:
    """Parse one session record into the views that display it."""
    content = _record_content(record)
    if record.role == "user":
        return [user_view(content)]
    if record.role == "assistant" and record.kind == "thinking":
        return [reasoning_view(content)] if content.strip() else []
    if record.role == "assistant" and record.kind == "answer":
        return [answer_view(content)] if content.strip() else []
    if record.role == "assistant" and record.kind == "error":
        return [error_view(content)]
    if record.role == "assistant" and record.kind == "compaction":
        dropped = (record.metadata or {}).get("dropped", 0)
        return [
            compaction_view(
                int(dropped) if isinstance(dropped, int) else 0, record.content
            )
        ]
    if record.role == "tool_call":
        tool_name = str((record.metadata or {}).get("tool_name", "tool"))
        return [tool_call_view(tool_name, record.content)]
    if record.role == "tool_output":
        metadata = record.metadata or {}
        return [
            tool_output_view(
                str(metadata.get("tool_name", "tool")),
                record.content,
                is_error=bool(metadata.get("is_error")),
            )
        ]
    return []


def _record_content(record: SessionRecord) -> str:
    if isinstance(record.content, str):
        return record.content
    return (
        "```json\n"
        + json.dumps(record.content, ensure_ascii=False, indent=2)
        + "\n```"
    )


def _call_summary(arguments: object) -> str:
    """工具调用的一行摘要:优先取常见主参数,否则压缩成单行 JSON。"""
    if isinstance(arguments, dict):
        for key in _SUMMARY_KEYS:
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return _one_line(value)
        if not arguments:
            return ""
        return _one_line(json.dumps(arguments, ensure_ascii=False))
    if isinstance(arguments, str):
        return _one_line(arguments)
    return _one_line(json.dumps(arguments, ensure_ascii=False))


def _preview(output: object) -> tuple[str, str]:
    """工具输出摘要:首个非空行 + 其余非空行数,供 ⎿ 行展示。"""
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return "(无输出)", ""
    extra = f"+{len(lines) - 1} 行" if len(lines) > 1 else ""
    return _one_line(lines[0]), extra


def _one_line(text: str, limit: int = _SUMMARY_MAX_CHARS) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _compact_number(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def _payload_markdown(value: object) -> str:
    """Fence a tool payload for display, clipping oversized content."""
    if isinstance(value, str):
        text, lang = value, ""
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2)
        lang = "json"
    if not text.strip():
        return "*（空）*"
    clipped, note = _clip(text)
    return _fenced(clipped, lang) + note


def _clip(text: str) -> tuple[str, str]:
    """Clip for display only; session records keep the full payload."""
    lines = text.splitlines()
    if len(lines) <= _TOOL_PAYLOAD_MAX_LINES and len(text) <= _TOOL_PAYLOAD_MAX_CHARS:
        return text, ""
    clipped = "\n".join(lines[:_TOOL_PAYLOAD_MAX_LINES])
    clipped = clipped[:_TOOL_PAYLOAD_MAX_CHARS].rstrip()
    note = f"\n\n*（已截断：完整内容共 {len(lines)} 行 / {len(text):,} 字符）*"
    return clipped, note


def _fenced(text: str, lang: str = "") -> str:
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{lang}\n{text}\n{fence}"
