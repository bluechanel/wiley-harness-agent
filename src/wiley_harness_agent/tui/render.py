"""Parse agent-layer records and usage into Markdown views for display."""

import json
from dataclasses import dataclass

from wiley_harness_agent.agent import ChatUsage, SessionRecord


@dataclass(frozen=True, slots=True)
class MessageView:
    """One renderable Markdown block plus its CSS classes."""

    markdown: str
    classes: str = ""


WELCOME_VIEW = MessageView("### 助手\n\n你好！我是你的 AI 助手。直接输入消息开始对话。")


def user_markdown(content: str) -> str:
    return f"### 你\n\n{content}"


def reasoning_markdown(text: str) -> str:
    return f"### 思考过程\n\n{text}"


def answer_markdown(text: str) -> str:
    return f"### 助手\n\n{text}"


def error_markdown(error: object) -> str:
    return f"### 错误\n\n`{error}`"


def usage_markdown(usage: ChatUsage, total: ChatUsage) -> str:
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


def render_record(record: SessionRecord) -> list[MessageView]:
    """Parse one session record into the Markdown views that display it."""
    content = _record_content(record)
    if record.role == "user":
        return [MessageView(user_markdown(content))]
    if record.role == "assistant" and record.kind == "thinking":
        return [MessageView(reasoning_markdown(content), classes="reasoning")]
    if record.role == "assistant" and record.kind == "answer":
        views = [MessageView(answer_markdown(content))]
        if record.usage and record.total_usage:
            views.append(
                MessageView(
                    usage_markdown(record.usage, record.total_usage),
                    classes="usage",
                )
            )
        return views
    if record.role == "assistant" and record.kind == "error":
        return [MessageView(error_markdown(content))]
    if record.role == "tool_call":
        tool_name = (record.metadata or {}).get("tool_name", "tool")
        return [
            MessageView(f"### 工具调用：{tool_name}\n\n{content}", classes="usage")
        ]
    if record.role == "tool_output":
        tool_name = (record.metadata or {}).get("tool_name", "tool")
        return [
            MessageView(f"### 工具输出：{tool_name}\n\n{content}", classes="usage")
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
