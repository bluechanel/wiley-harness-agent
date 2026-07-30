"""wy-realtime-agent 测试辅助:脚本化假 WebSocket、假音频与事件收集器。

仓库测试不引入 pytest-asyncio:async 流程用 ``run_agent``(内部
``asyncio.run``)收集事件。假件只做鸭子类型,不继承真实类。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from wy_core import RealtimeAgent, RealtimeEvent

from wy_realtime_agent.config import RealtimeConfig
from wy_realtime_agent.protocol import RealtimeClient
from wy_realtime_agent.qwen import QwenRealtimeModel

_WAIT_TIMEOUT_SECONDS = 5.0


@dataclass
class WaitFor:
    """脚本控制项:等谓词满足后再继续吐后续服务端事件(用于与发送任务同步)。

    谓词接收 FakeWebSocket 本体,便于对 ``sent`` 做条件等待。
    """

    predicate: Callable[["FakeWebSocket"], bool]


class FakeWebSocket:
    """脚本化服务端:async 迭代逐个吐出脚本事件,send() 记录客户端事件。"""

    def __init__(self, script: Sequence[dict | WaitFor] = ()) -> None:
        self._script = list(script)
        self.sent: list[dict] = []
        self.closed = False
        self.connect_url: str | None = None
        self.connect_headers: dict[str, str] | None = None

    def connector(self):
        """返回可注入 RealtimeClient 的连接工厂,记录建连参数。"""

        async def _connect(url: str, headers: dict[str, str]) -> FakeWebSocket:
            self.connect_url = url
            self.connect_headers = dict(headers)
            return self

        return _connect

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for entry in self._script:
            if isinstance(entry, WaitFor):
                deadline = time.monotonic() + _WAIT_TIMEOUT_SECONDS
                while not entry.predicate(self):
                    if time.monotonic() > deadline:
                        raise AssertionError("WaitFor 超时:谓词始终未满足")
                    await asyncio.sleep(0.001)
                continue
            await asyncio.sleep(0)
            yield json.dumps(entry, ensure_ascii=False)

    def sent_of_type(self, event_type: str) -> list[dict]:
        return [event for event in self.sent if event.get("type") == event_type]


class FakeMic:
    """假麦克风:按脚本吐块,耗尽后返回 None;记录读取次数与生命周期。"""

    def __init__(self, chunks: Sequence[bytes] = ()) -> None:
        self._chunks = list(chunks)
        self.reads = 0
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def read(self, timeout: float = 1.0) -> bytes | None:
        self.reads += 1
        time.sleep(0.001)  # 模拟阻塞节拍,避免发送任务空转垄断事件循环
        return self._chunks.pop(0) if self._chunks else None

    def stop(self) -> None:
        self.stopped = True


class FakeSpeaker:
    """假扬声器:记录播放与清空调用,is_playing 可由测试设定。"""

    def __init__(self) -> None:
        self.played: list[bytes] = []
        self.cleared = 0
        self.playing = False
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def play(self, pcm: bytes) -> None:
        self.played.append(pcm)

    def clear(self) -> None:
        self.cleared += 1

    def is_playing(self) -> bool:
        return self.playing

    def stop(self) -> None:
        self.stopped = True


def make_config(**overrides) -> RealtimeConfig:
    defaults = dict(url="wss://example.test/realtime", api_key="key", model="test-model")
    defaults.update(overrides)
    return RealtimeConfig(**defaults)


def make_agent(
    script: Sequence[dict | WaitFor],
    *,
    config: RealtimeConfig | None = None,
    tools=(),
    mic: FakeMic | None = None,
    speaker: FakeSpeaker | None = None,
    audit=None,
) -> tuple[RealtimeAgent, FakeWebSocket]:
    """组装接假 ws/音频的 QwenRealtimeModel + RealtimeAgent;audit 缺省关闭,避免污染 CWD。"""
    config = config if config is not None else make_config()
    ws = FakeWebSocket(script)
    client = RealtimeClient(config.url, config.api_key, config.model, connect=ws.connector())
    agent = RealtimeAgent(
        model=QwenRealtimeModel(config, client=client),
        tools=tools,
        system=config.instructions or None,
        mic=mic if mic is not None else FakeMic(),
        speaker=speaker if speaker is not None else FakeSpeaker(),
        echo_suppression=config.echo_suppression,
        audit=audit,
    )
    return agent, ws


def run_agent(
    agent: RealtimeAgent,
    on_event: Callable[[RealtimeEvent], Awaitable[None]] | None = None,
) -> list[RealtimeEvent]:
    """同步跑完一次 run(),收集全部 RealtimeEvent。

    ``on_event``(async)在每个事件产出后调用,用于中途驱动 agent(如注入
    文字消息);此时 run() 挂起在 yield 点,回调内观察到的状态是确定性的。
    """

    async def go() -> list[RealtimeEvent]:
        events: list[RealtimeEvent] = []
        async for event in agent.run():
            events.append(event)
            if on_event is not None:
                await on_event(event)
        return events

    return asyncio.run(go())
