"""工具抽象父类、工具执行事件与工具审批钩子。

``Tool`` 是模型可调用工具的 API schema 加本地执行器;``ToolCall``/
``ToolResult`` 是两类 agent(回合式 ``Agent`` 与实时 ``RealtimeAgent``)
共用的工具执行事件;``ToolHook`` 是工具审批抽象——Agent 与 RealtimeAgent
在执行工具前调用 ``approve``,可批准或拒绝调用。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


class Tool(ABC):
    """继承本类、定义三个类属性并实现 execute,即得到一个工具。

    - ``name`` 工具唯一名;``description`` 给模型看的用途说明;
      ``parameters`` 入参 JSON Schema(Anthropic 的 input_schema、
      OpenAI 的 parameters,由 Model 实现映射到厂商字段)。
    - ``execute`` 为同步方法,允许阻塞(Agent 经 asyncio.to_thread
      执行,不会冻结事件循环);失败直接 raise,由 Agent 统一转
      ``Error: ...`` 的 tool_result(is_error=True)返回给模型,
      不中断回合。
    """

    name: str
    description: str
    parameters: dict

    @abstractmethod
    def execute(self, input: dict) -> str:
        """执行工具,返回给模型看的文本结果。"""


@dataclass
class ToolCall:
    """工具即将执行。"""

    id: str
    name: str
    input: dict


@dataclass
class ToolResult:
    """工具执行完毕。"""

    id: str
    name: str
    content: str
    is_error: bool


@dataclass
class ToolApproval:
    """工具调用审批结果。

    - ``allowed`` True 允许执行;False 否决,``reason`` 写入 tool_result 错误文本。
    - ``reason`` 否决原因或通过备注,允许为空。
    """

    allowed: bool
    reason: str = ""


class ToolHook(ABC):
    """继承本类实现工具审批:Agent 与 RealtimeAgent 在执行工具前调用 approve。

    返回 ToolApproval:allowed=True 允许执行,False 否决。
    否决时产生 ToolResult(is_error=True, content="工具调用被拒绝: {reason}")。
    审批本身抛异常视为否决(reason 为异常信息),不中断回合。
    approve 为异步方法,实现方可做 I/O(弹对话框、查策略等)。
    """

    @abstractmethod
    async def approve(self, call: ToolCall) -> ToolApproval:
        """审批一次工具调用。Agent 并发场景下可能被多个协程同时调用。"""
