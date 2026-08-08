"""工具抽象父类、工具执行事件与工具审批钩子。

``Tool`` 是模型可调用工具的 API schema 加本地执行器;``ToolCall``/
``ToolResult`` 是两类 agent(回合式 ``Agent`` 与实时 ``RealtimeAgent``)
共用的工具执行事件;``ToolHook`` 是工具审批抽象——Agent 与 RealtimeAgent
在执行工具前调用 ``approve``,可批准或拒绝调用。

``ApprovalRequest`` 是 ``Tool.approve()`` 的返回值:None 表示直接放行,
返回本对象则表示需要用户审批,同时携带审批 UI 所需的展示信息与"不再询问"
记忆标识。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ApprovalRequest:
    """``Tool.approve()`` 的返回值:需要审批时返回本对象,放行时返回 None。

    ``heading``/``question`` 供 UI 渲染审批卡片;``fields`` 是展示给用户的
    参数列表 ``[(标签, 值), ...]``;``key`` 为"本次会话不再询问"的稳定
    记忆标识(None 表示不可记忆)。
    """

    heading: str
    question: str
    fields: list[tuple[str, str]] = field(default_factory=list)
    key: str | None = None


class Tool(ABC):
    """继承本类、定义三个类属性并实现 execute,即得到一个工具。

    - ``name`` 工具唯一名;``description`` 给模型看的用途说明;
      ``parameters`` 入参 JSON Schema(Anthropic 的 input_schema、
      OpenAI 的 parameters,由 Model 实现映射到厂商字段)。
    - ``execute`` 为同步方法,允许阻塞(Agent 经 asyncio.to_thread
      执行,不会冻结事件循环);失败直接 raise,由 Agent 统一转
      ``Error: ...`` 的 tool_result(is_error=True)返回给模型,
      不中断回合。
    - ``approve`` 为审批入口:默认返回 None(放行);需要审批的工具覆写
      本方法,返回 ``ApprovalRequest`` 携带展示信息与记忆标识。
    """

    name: str
    description: str
    parameters: dict

    @abstractmethod
    def execute(self, input: dict) -> str:
        """执行工具,返回给模型看的文本结果。"""

    def approve(self, input: dict, workspace: Path) -> ApprovalRequest | None:
        """审批检查。返回 None 直接放行;返回 ApprovalRequest 进入审批流程。

        ``workspace`` 为当前工作区路径,路径相关工具用它判断工作区内/外;
        其余工具忽略即可。
        """
        return None


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
