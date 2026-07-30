"""实时(realtime)模型抽象父类与类型化事件词汇。

端到端语音模型走服务端维护上下文、VAD 主动触发响应的推送式全双工流,
与 ``Model.stream`` 的拉取式回合契约不匹配,故单独成契约:使用方继承
``RealtimeModel`` 适配任意厂商的实时协议,把厂商 wire 事件翻译为本模块
的类型化事件,即可交给 ``RealtimeAgent`` 编排。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from wy_core.tool import Tool


class RealtimeError(RuntimeError):
    """实时连接失败:握手、传输或异常关闭。"""


@dataclass
class ResponseStarted:
    """服务端开始产出一次回复。"""

    response_id: str | None = None


@dataclass
class AudioDelta:
    """一段回复语音增量(已解码的 PCM 字节)。"""

    pcm: bytes


@dataclass
class SpeechStarted:
    """服务端 VAD 检测到用户开始说话。"""


@dataclass
class UserTranscript:
    """一段用户语音的转写已完成。"""

    text: str


@dataclass
class AssistantTranscript:
    """一次模型语音回复的转写已完成。"""

    text: str


@dataclass
class TurnDiscarded:
    """服务端判定刚才的语音不构成有效轮次,不会产出回复。"""


@dataclass
class FunctionCall:
    """模型请求执行一个工具(入参已解析为 dict)。"""

    call_id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class ResponseDone:
    """一次回复结束;被打断取消时 cancelled 为 True。"""

    cancelled: bool = False


@dataclass
class ErrorEvent:
    """服务端错误事件(非致命,连接仍在;致命错误随后表现为连接关闭)。"""

    type: str
    message: str


RealtimeModelEvent = (
    ResponseStarted
    | AudioDelta
    | SpeechStarted
    | UserTranscript
    | AssistantTranscript
    | TurnDiscarded
    | FunctionCall
    | ResponseDone
    | ErrorEvent
)


class RealtimeModel(ABC):
    """全双工实时模型契约。实现方义务(本 docstring 即约定本体):

    - 厂商参数(endpoint、鉴权、音色、VAD 参数、历史轮数……)全部在
      实现类构造期注入;本契约只承载逐会话变化的状态(system、tools)
      与运行期收发。
    - ``events()`` 负责把厂商 wire 事件翻译为 ``RealtimeModelEvent``
      词汇逐个产出;词汇之外的 wire 事件不产出(静默忽略)。
      ``AudioDelta.pcm`` 必须是已解码的 PCM 字节;``FunctionCall.
      arguments`` 必须是已解析的 dict(残缺入参回退空 dict)。
    - 服务端正常关闭时 ``events()`` 自然结束;握手 / 传输 / 异常关闭
      一律 raise ``RealtimeError``,未建连时调用发送类方法同样如此。
    - ``update_session`` 由 Agent 在 ``connect`` 之后、消费事件之前
      调用恰好一次,下发 system 指令与工具集,返回实际发送给厂商的
      会话载荷(供审计留痕);``tools`` 只读取 name / description /
      parameters 生成厂商 schema,永不调用 execute。
    - ``close()`` 幂等,未建连也可安全调用。
    """

    name: str = ""  # 模型标识,仅用于展示与审计

    @abstractmethod
    async def connect(self) -> None:
        """建立实时连接。"""

    @abstractmethod
    async def update_session(
        self, *, system: str | None = None, tools: Sequence[Tool] = ()
    ) -> dict:
        """下发会话配置,返回实际发送的载荷。"""

    @abstractmethod
    async def send_audio(self, pcm: bytes) -> None:
        """流式推送一块用户语音 PCM。"""

    @abstractmethod
    async def send_user_text(self, text: str) -> None:
        """注入一条 user 文字消息(不触发响应)。"""

    @abstractmethod
    async def send_tool_result(self, call_id: str, output: str) -> None:
        """回写一个工具执行结果(不触发响应)。"""

    @abstractmethod
    async def create_response(self) -> None:
        """请求模型立即开始一次回复。"""

    @abstractmethod
    async def cancel_response(self) -> None:
        """取消进行中的回复(打断)。"""

    @abstractmethod
    def events(self) -> AsyncIterator[RealtimeModelEvent]:
        """逐个产出服务端事件,直到连接结束。"""

    @abstractmethod
    async def close(self) -> None:
        """关闭连接;幂等。"""
