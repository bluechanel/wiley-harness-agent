"""流式音频 IO 抽象:实时 agent 的麦克风采集与扬声器播放契约。

PCM 一律为 16bit(int16)单声道字节;采样率不属于本契约,由音频实现与
``RealtimeModel`` 实现两侧按厂商协议自行约定,core 只搬运字节。
"""

from abc import ABC, abstractmethod


class AudioSource(ABC):
    """音频输入源(如麦克风)。实现方义务(本 docstring 即约定本体):

    - ``read`` 为同步阻塞方法(Agent 经 asyncio.to_thread 调用,不会
      冻结事件循环),超时返回 None,供调用方检查退出条件后重试;
    - ``start``/``stop`` 幂等,由 Agent 在会话进出时成对调用。
    """

    @abstractmethod
    def start(self) -> None:
        """开始采集。"""

    @abstractmethod
    def read(self, timeout: float = 1.0) -> bytes | None:
        """取一块 PCM;超时返回 None。"""

    @abstractmethod
    def stop(self) -> None:
        """停止采集并释放设备。"""


class AudioSink(ABC):
    """音频输出汇(如扬声器)。实现方义务(本 docstring 即约定本体):

    - ``play`` 不得阻塞事件循环(入队即返回,后台顺序播放);
    - ``clear`` 丢弃全部未播数据,用于打断,残余播放延迟应尽量小;
    - ``is_playing`` 报告是否仍在播/有待播数据,供回声抑制判断;
    - ``start``/``stop`` 幂等,由 Agent 在会话进出时成对调用。
    """

    @abstractmethod
    def start(self) -> None:
        """开始播放服务。"""

    @abstractmethod
    def play(self, pcm: bytes) -> None:
        """入队一段 PCM(任意长度)。"""

    @abstractmethod
    def clear(self) -> None:
        """打断:丢弃全部未播数据。"""

    @abstractmethod
    def is_playing(self) -> bool:
        """是否仍在播放或有待播数据。"""

    @abstractmethod
    def stop(self) -> None:
        """停止播放并释放设备。"""
