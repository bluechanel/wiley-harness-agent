"""wy-core realtime 测试辅助:脚本化假实时模型、假音频与事件收集器。

仓库测试不引入 pytest-asyncio:async 流程用 ``run_realtime``(内部
``asyncio.run``,可传 async ``on_event`` 回调在事件产出点中途驱动
agent,如注入文字指令)收集事件。假件只做鸭子类型,不继承真实类。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from wy_core import RealtimeAgent, RealtimeError, RealtimeEvent, RealtimeModelEvent, Tool

_WAIT_TIMEOUT_SECONDS = 5.0


@dataclass
class WaitFor:
    """脚本控制项:等谓词满足后再继续吐后续模型事件(用于与发送任务同步)。

    谓词接收 FakeRealtimeModel 本体,便于对 ``sent`` 做条件等待。
    """

    predicate: Callable[["FakeRealtimeModel"], bool]


class FakeRealtimeModel:
    """脚本化假实时模型:events() 逐个吐出脚本事件,发送类方法记录到 sent。

    ``sent`` 按调用顺序记录 (kind, payload) 二元组,kind 取
    "session.update"/"audio"/"user_text"/"tool_result"/"response.create"/
    "response.cancel"。脚本项可以是 RealtimeModelEvent、WaitFor 或异常
    (吐到该项时原样抛出,模拟传输失败)。
    """

    name = "fake-realtime"

    def __init__(
        self, script: Sequence[RealtimeModelEvent | WaitFor | BaseException] = ()
    ) -> None:
        self._script = list(script)
        self.sent: list[tuple[str, object]] = []
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def update_session(self, *, system=None, tools: Sequence[Tool] = ()) -> dict:
        self._ensure_connected()
        payload = {"instructions": system, "tools": [tool.name for tool in tools]}
        self.sent.append(("session.update", payload))
        return payload

    async def send_audio(self, pcm: bytes) -> None:
        self._ensure_connected()
        self.sent.append(("audio", pcm))

    async def send_user_text(self, text: str) -> None:
        self._ensure_connected()
        self.sent.append(("user_text", text))

    async def send_tool_result(self, call_id: str, output: str) -> None:
        self._ensure_connected()
        self.sent.append(("tool_result", (call_id, output)))

    async def create_response(self) -> None:
        self._ensure_connected()
        self.sent.append(("response.create", None))

    async def cancel_response(self) -> None:
        self._ensure_connected()
        self.sent.append(("response.cancel", None))

    async def close(self) -> None:
        self.closed = True

    def _ensure_connected(self) -> None:
        if not self.connected:
            raise RealtimeError("FakeRealtimeModel 尚未连接")

    async def events(self):
        for entry in self._script:
            if isinstance(entry, WaitFor):
                deadline = time.monotonic() + _WAIT_TIMEOUT_SECONDS
                while not entry.predicate(self):
                    if time.monotonic() > deadline:
                        raise AssertionError("WaitFor 超时:谓词始终未满足")
                    await asyncio.sleep(0.001)
                continue
            if isinstance(entry, BaseException):
                raise entry
            await asyncio.sleep(0)
            yield entry

    def sent_of_type(self, kind: str) -> list:
        return [payload for sent_kind, payload in self.sent if sent_kind == kind]

    def indexes_of(self, kind: str) -> list[int]:
        return [i for i, (sent_kind, _payload) in enumerate(self.sent) if sent_kind == kind]


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


def make_realtime_agent(
    script: Sequence[RealtimeModelEvent | WaitFor | BaseException] = (),
    *,
    tools=(),
    system: str | None = None,
    mic: FakeMic | None = None,
    speaker: FakeSpeaker | None = None,
    echo_suppression: bool = True,
    audit=None,
) -> tuple[RealtimeAgent, FakeRealtimeModel]:
    """组装接假模型/假音频的 RealtimeAgent;audit 缺省关闭,避免污染 CWD。"""
    model = FakeRealtimeModel(script)
    agent = RealtimeAgent(
        model=model,
        tools=tools,
        system=system,
        mic=mic if mic is not None else FakeMic(),
        speaker=speaker if speaker is not None else FakeSpeaker(),
        echo_suppression=echo_suppression,
        audit=audit,
    )
    return agent, model


def run_realtime(
    agent: RealtimeAgent,
    on_event: Callable[[RealtimeEvent], Awaitable[None]] | None = None,
) -> list[RealtimeEvent]:
    """同步跑完一次 run(),收集全部 RealtimeEvent。

    ``on_event``(async)在每个事件产出后调用,用于中途驱动 agent(如注入
    文字指令);此时 run() 挂起在 yield 点,回调内观察到的状态是确定性的。
    """

    async def go() -> list[RealtimeEvent]:
        events: list[RealtimeEvent] = []
        async for event in agent.run():
            events.append(event)
            if on_event is not None:
                await on_event(event)
        return events

    return asyncio.run(go())
