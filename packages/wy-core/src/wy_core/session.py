"""内存态会话上下文:消息列表、用量统计与自动压缩。

仅内存态:落盘与恢复由使用方自行实现(审计日志本身已完整留痕)。
"""

from __future__ import annotations

from wy_core.message import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
    user_message,
)
from wy_core.model import Model, ModelEnd, ModelError

_COMPACT_SYSTEM = (
    "你是对话压缩助手。请把用户提供的对话记录压缩为一份要点摘要,"
    "必须保留:任务目标、关键决策与结论、已完成与未完成事项、"
    "重要的文件路径与数据引用。直接输出摘要正文,不要任何前后缀。"
)


class Session:
    """会话状态容器:消息历史、用量与自动上下文压缩。"""

    def __init__(self, *, max_context_tokens: int = 150_000, keep_recent: int = 8) -> None:
        self.messages: list[Message] = []
        self.context_tokens = 0  # 最近一次请求后的上下文规模(压缩后归 0,下轮刷新)
        self.total_usage = Usage()
        self.max_context_tokens = max_context_tokens
        self.keep_recent = keep_recent

    def append(self, message: Message) -> None:
        self.messages.append(message)

    def record_usage(self, usage: Usage) -> None:
        self.context_tokens = usage.context_tokens
        self.total_usage.add(usage)

    def needs_compaction(self) -> bool:
        return self.context_tokens >= self.max_context_tokens and self._split_point() > 0

    async def compact(self, model: Model) -> dict:
        """用模型把较早的历史压缩为一条摘要消息,返回审计信息。"""
        split = self._split_point()
        if split <= 0:
            return {"dropped": 0, "summary": ""}
        head, kept = self.messages[:split], self.messages[split:]
        summary = await self._summarize(model, head)
        self.messages = [user_message("[早前对话摘要]\n" + summary), *kept]
        self.context_tokens = 0
        return {"dropped": split, "summary": summary}

    def _split_point(self) -> int:
        """保留最近 keep_recent 条;保留段以 tool_result 开头时前移,不拆散工具对。"""
        split = len(self.messages) - self.keep_recent
        while split > 0 and _has_tool_result(self.messages[split]):
            split -= 1
        return max(split, 0)

    async def _summarize(self, model: Model, head: list[Message]) -> str:
        """把 head 渲染为纯文本对话记录请模型压缩,不依赖厂商的消息交替规则。"""
        transcript = "\n".join(_render(m) for m in head)
        request = [user_message("以下是需要压缩的对话记录:\n\n" + transcript)]
        async for event in model.stream(request, system=_COMPACT_SYSTEM):
            if isinstance(event, ModelEnd):
                return event.message.text
        raise ModelError("压缩请求的模型流未产出 ModelEnd")


def _has_tool_result(message: Message) -> bool:
    return any(isinstance(b, ToolResultBlock) for b in message.content)


def _render(message: Message) -> str:
    """把一条消息渲染为对话记录文本;思考块不属于需要留存的事实,跳过。"""
    lines = [f"[{message.role}]"]
    for block in message.content:
        if isinstance(block, TextBlock):
            lines.append(block.text)
        elif isinstance(block, ToolUseBlock):
            lines.append(f"调用工具 {block.name}({block.input})")
        elif isinstance(block, ToolResultBlock):
            lines.append(f"工具结果: {block.content}")
    return "\n".join(lines)
