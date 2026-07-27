"""对外统一的消息定义:内容块、Message 与 Usage。

整个 wy-core(模型契约、会话历史、agent 事件、审计日志)只使用这一套
消息词汇。块 schema 取 Anthropic 风格的中立形态,非 Anthropic 后端由
Model 实现自行完成两侧格式翻译。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class TextBlock:
    """正文文本块。"""

    text: str
    type: str = "text"


@dataclass
class ThinkingBlock:
    """思考块;signature 由厂商实现按需填写(如 Anthropic 的回放校验)。"""

    thinking: str
    signature: str = ""
    type: str = "thinking"


@dataclass
class ToolUseBlock:
    """assistant 消息中的工具调用请求。"""

    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class ToolResultBlock:
    """user 消息中携带的工具执行结果。"""

    tool_use_id: str
    content: str
    is_error: bool = False
    type: str = "tool_result"


Block = TextBlock | ThinkingBlock | ToolUseBlock | ToolResultBlock


@dataclass
class Message:
    """一条会话消息:role 为 "user" 或 "assistant",content 为内容块列表。"""

    role: str
    content: list[Block]

    @property
    def text(self) -> str:
        """拼接全部文本块,供展示与压缩摘要使用。"""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    def to_dict(self) -> dict:
        """转为可 JSON 序列化的字典;块自带 type 标签,自描述。"""
        return asdict(self)


def user_message(text: str) -> Message:
    """构造纯文本 user 消息。"""
    return Message(role="user", content=[TextBlock(text)])


@dataclass
class Usage:
    """一次(或累计的)模型请求用量。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def context_tokens(self) -> int:
        """四个分量之和,近似为本次请求完成后的上下文规模。"""
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def add(self, other: Usage) -> None:
        """把 other 累加进自身,用于会话累计用量。"""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
