"""LLM 接入抽象父类与流事件。

wy-core 只定义契约:使用方继承 ``Model`` 适配任意厂商 API,
按本模块的事件语义产出流。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass

from wy_core.message import Message, Usage
from wy_core.tool import Tool


class ModelError(RuntimeError):
    """模型请求失败:传输、解码或流内厂商错误。"""


@dataclass
class TextDelta:
    """正文文本增量,仅供实时渲染。"""

    text: str


@dataclass
class ThinkingDelta:
    """思考文本增量,仅供实时渲染。"""

    thinking: str


@dataclass
class ModelEnd:
    """流结束:组装完成的 assistant 消息、本次用量与停止原因。"""

    message: Message
    usage: Usage
    stop_reason: str


ModelEvent = TextDelta | ThinkingDelta | ModelEnd


class Model(ABC):
    """异步流式模型契约。实现方义务(本 docstring 即约定本体):

    - 厂商参数(模型名、鉴权、max_tokens、endpoint、temperature……)
      全部在实现类构造期注入;``stream`` 签名固定,只接收逐轮变化的
      会话状态。
    - 增量事件仅供 UI 实时渲染,权威内容在 ``ModelEnd.message``:
      实现方负责把厂商流组装为完整 assistant 消息(thinking/text/
      tool_use 块;thinking signature 等厂商字段也在此处填入)。
    - 每次流必须恰好以一个 ``ModelEnd`` 结束,且它是最后一个事件。
    - 模型请求执行工具时 ``stop_reason`` 必须为 ``"tool_use"``
      (驱动 Agent 的工具循环),其余取值一律视为回合结束。
    - 传输 / 解码 / 流内厂商错误一律 raise ``ModelError``。
    - ``tools`` 只读取 name / description / parameters 生成厂商
      schema,永不调用 execute;None 表示本次请求不带工具。
    """

    name: str = ""  # 模型标识,仅用于展示与审计

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: Sequence[Tool] | None = None,
    ) -> AsyncIterator[ModelEvent]:
        """执行一次流式请求,逐个产出 ModelEvent。"""
