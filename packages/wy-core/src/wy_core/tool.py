"""工具抽象父类:模型可调用工具的 API schema 加本地执行器。"""

from abc import ABC, abstractmethod


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
