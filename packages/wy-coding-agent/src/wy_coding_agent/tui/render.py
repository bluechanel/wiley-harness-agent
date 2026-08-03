"""Parse agent-layer records and usage into display views for the TUI."""

import json
import re
from dataclasses import dataclass

from wy_core import Usage

from wy_coding_agent.session import SessionRecord


@dataclass(frozen=True, slots=True)
class MessageView:
    """One renderable Markdown block plus its presentation hints.

    collapsible_title 非空表示该块默认收起，点击标题行展开/收起。
    """

    markdown: str
    classes: str = ""
    collapsible_title: str = ""


WELCOME_VIEW = MessageView("### 助手\n\n你好！我是你的 AI 助手。直接输入消息开始对话。")

PLAN_MODE_VIEW = MessageView(
    "### 系统\n\n已进入 plan 模式：助手将只调研与设计，"
    "完成后经 `exit_plan_mode` 提交计划并自动退出。"
)

_TOOL_PAYLOAD_MAX_LINES = 30
_TOOL_PAYLOAD_MAX_CHARS = 2000


def user_markdown(content: str) -> str:
    return f"### 你\n\n{content}"


def answer_markdown(text: str) -> str:
    return f"### 助手\n\n{text}"


def error_markdown(error: object) -> str:
    return f"### 错误\n\n`{error}`"


def usage_bar_text(total: Usage, context_tokens: int) -> str:
    """One-line usage summary: accumulated totals plus the live context size."""
    cache_tokens = total.cache_read_tokens + total.cache_write_tokens
    return (
        f"输入 {total.input_tokens:,} · "
        f"输出 {total.output_tokens:,} · "
        f"缓存 {cache_tokens:,} · "
        f"上下文 {context_tokens:,} tokens"
    )


def reasoning_view(text: str) -> MessageView:
    return MessageView(text, classes="reasoning", collapsible_title="思考过程")


def tool_call_view(tool_name: str, arguments: object) -> MessageView:
    if tool_name == "exit_plan_mode" and isinstance(arguments, dict):
        plan = str(arguments.get("plan", ""))
        if plan.strip():
            # 计划是给用户审阅的正文,直接以 Markdown 展开,不折叠不围栏。
            return MessageView(f"### 📋 计划\n\n{plan}")
    return MessageView(
        _payload_markdown(arguments),
        classes="tool",
        collapsible_title=f"工具调用：{tool_name}",
    )


def tool_output_view(
    tool_name: str, output: object, *, is_error: bool = False
) -> MessageView:
    heading = "工具输出（错误）" if is_error else "工具输出"
    return MessageView(
        _payload_markdown(output),
        classes="tool",
        collapsible_title=f"{heading}：{tool_name}",
    )


def compaction_view(dropped: int, summary: object) -> MessageView:
    return MessageView(
        _payload_markdown(summary),
        classes="tool",
        collapsible_title=f"上下文压缩：已总结 {dropped} 条早前消息",
    )


def render_record(record: SessionRecord) -> list[MessageView]:
    """Parse one session record into the views that display it."""
    content = _record_content(record)
    if record.role == "user":
        return [MessageView(user_markdown(content))]
    if record.role == "assistant" and record.kind == "thinking":
        return [reasoning_view(content)]
    if record.role == "assistant" and record.kind == "answer":
        return [MessageView(answer_markdown(content))]
    if record.role == "assistant" and record.kind == "error":
        return [MessageView(error_markdown(content))]
    if record.role == "assistant" and record.kind == "compaction":
        dropped = (record.metadata or {}).get("dropped", 0)
        return [compaction_view(int(dropped) if isinstance(dropped, int) else 0, record.content)]
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
