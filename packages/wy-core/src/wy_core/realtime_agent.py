"""实时 agent 编排:实时模型 + 流式音频 + 工具执行 + 审计。

与 ``Agent`` 的关系:实时协议是服务端维护上下文、VAD 主动触发响应的
推送式全双工流,不适配 ``Model.stream``/``Agent.run`` 的拉取式回合契约,
故自成循环;但工具执行语义与事件词汇对齐——工具按 ``Tool`` 契约编写,
经 ``asyncio.to_thread`` 同步执行,失败转 ``Error: ...`` 文本不中断会话,
工具事件复用 ``ToolCall``/``ToolResult``,审计走 ``AuditLog``(语义级
事件,不逐 delta 留痕)。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import cast

from wy_core.audio import AudioSink, AudioSource
from wy_core.log import AuditLog
from wy_core.realtime_model import (
    AssistantTranscript,
    AudioDelta,
    ErrorEvent,
    FunctionCall,
    RealtimeError,
    RealtimeModel,
    RealtimeModelEvent,
    ResponseDone,
    ResponseStarted,
    SpeechStarted,
    TurnDiscarded,
    UserTranscript,
)
from wy_core.tool import Tool, ToolCall, ToolResult


@dataclass
class Interrupted:
    """用户语音打断了正在进行的回复。"""

    response_id: str | None


@dataclass
class SessionEnded:
    """连接结束(服务端关闭或传输错误),run() 以此收尾。"""

    reason: str


RealtimeEvent = (
    UserTranscript | AssistantTranscript | ToolCall | ToolResult | Interrupted | SessionEnded
)

_ECHO_COOLDOWN_SECONDS = 0.5  # 播放结束后的闭麦冷却,防扬声器尾音触发 VAD
_NOISE_GATE_THRESHOLD = 500  # 耳机模式:回复期间的能量门限,低于视为回声/噪声

_DEFAULT_AUDIT = cast(AuditLog, object())  # 哨兵:区分"未传 audit"与显式 None


def _audio_energy(pcm: bytes) -> float:
    """int16 PCM 的平均绝对幅度(3.13 已移除 audioop,自行实现)。"""
    samples = memoryview(pcm[: len(pcm) & ~1]).cast("h")
    if len(samples) == 0:
        return 0.0
    return sum(abs(sample) for sample in samples) / len(samples)


class RealtimeAgent:
    """把 RealtimeModel、音频 IO 与 Tool 组装为完整实时语音 agent。

    审计默认开启:省略 ``audit`` 即写入 CWD/.wy_audit/,显式传 ``audit=None``
    关闭。单个实例不支持并发 ``run``;``run`` 期间可经 ``send_user_text``
    注入后台文字指令(空闲时注入并立即触发响应,忙碌时排队);``close()``
    释放组装方注入的资源(如 MCP 连接),幂等,宿主 finally 里调用。
    """

    def __init__(
        self,
        *,
        model: RealtimeModel,
        tools: Sequence[Tool] = (),
        system: str | None = None,
        mic: AudioSource,
        speaker: AudioSink,
        echo_suppression: bool = True,
        audit: AuditLog | None = _DEFAULT_AUDIT,
        closer: Callable[[], None] | None = None,
    ) -> None:
        self.model = model
        self.tools = {t.name: t for t in tools}
        if len(self.tools) != len(tools):
            raise ValueError("工具名重复")
        self.system = system
        self.mic = mic
        self.speaker = speaker
        self.echo_suppression = echo_suppression
        self.audit = AuditLog.default() if audit is _DEFAULT_AUDIT else audit
        self._closer = closer
        self._is_responding = False
        self._current_response_id: str | None = None
        self._audio_suppressed = False
        self._listening = False
        self._response_pending = False
        self._pending_calls: list[FunctionCall] = []
        self._pending_texts: list[str] = []
        self._audit(
            "agent_start",
            {
                "model": model.name,
                "tools": list(self.tools),
                "echo_suppression": echo_suppression,
            },
        )

    async def run(self) -> AsyncIterator[RealtimeEvent]:
        """建连并进行实时对话,流式产出 RealtimeEvent。

        服务端正常关闭或传输失败以 ``SessionEnded`` 收尾;其余异常(含消费
        方中途关闭流)写一条 error 审计后原样上抛。出口统一收尾:停发送
        任务、停音频、关连接。
        """
        sender: asyncio.Task | None = None
        try:
            await self.model.connect()
            session = await self.model.update_session(
                system=self.system, tools=list(self.tools.values())
            )
            self._audit("session_update", {"session": session})

            self.mic.start()
            self.speaker.start()
            sender = asyncio.create_task(self._send_audio(), name="mic-sender")

            async for event in self.model.events():
                async for out in self._handle_event(event):
                    yield out
            yield SessionEnded(reason="服务端关闭了连接")
        except RealtimeError as exc:
            self._audit("error", {"type": type(exc).__name__, "error": str(exc)})
            yield SessionEnded(reason=str(exc))
        except BaseException as exc:
            self._audit("error", {"type": type(exc).__name__, "error": str(exc)})
            raise
        finally:
            if sender is not None:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await sender
            self.mic.stop()
            self.speaker.stop()
            await self.model.close()

    def close(self) -> None:
        """释放组装方注入的资源(如 MCP 连接);幂等。"""
        if self._closer is not None:
            closer, self._closer = self._closer, None
            closer()

    async def send_user_text(self, text: str) -> None:
        """注入一条后台文字指令(role=user)并让模型立即执行。

        仅在空闲——既不在听(SpeechStarted 起,到该轮 ResponseStarted 或
        TurnDiscarded 判非轮次止)也不在答(ResponseStarted→ResponseDone)
        ——时注入,注入后紧跟一次响应触发让模型开始执行;忙碌期间先入队,
        待回合真正结束(无待执行工具的 ResponseDone,或 TurnDiscarded)后
        按序补发,并只触发一次响应。须在 ``run()`` 所在事件循环中调用;
        尚未建连时抛 RealtimeError。
        """
        self._pending_texts.append(text)
        await self._flush_pending_texts()

    async def _flush_pending_texts(self) -> None:
        """空闲时把排队的后台指令全部注入并触发一次响应;忙碌时按兵不动。"""
        busy = self._listening or self._is_responding or self._response_pending
        if busy or not self._pending_texts:
            return
        texts, self._pending_texts = self._pending_texts, []
        for text in texts:
            await self.model.send_user_text(text)
            self._audit("user_text", {"text": text})
        await self._request_response()

    async def _request_response(self) -> None:
        """触发一次响应并标记响应待建,到 ResponseStarted 为止。

        标记期间新指令只入队不再触发,避免连续两次触发撞出"已有进行中
        响应"类服务端错误。
        """
        self._response_pending = True
        await self.model.create_response()

    async def _send_audio(self) -> None:
        """持续把麦克风 PCM 推给模型,按配置做回声抑制。

        - echo_suppression=True(免耳机):回复/播放期间闭麦丢块,结束后再
          冷却 0.5s,不支持语音打断;
        - echo_suppression=False(耳机):回复/播放期间用能量门限滤掉回声
          与噪声,高能量语音照发,支持打断。
        """
        playback_end: float | None = None
        while True:
            chunk = await asyncio.to_thread(self.mic.read)
            if chunk is None:
                continue
            active = self._is_responding or self.speaker.is_playing()
            if self.echo_suppression:
                if active:
                    playback_end = time.monotonic()
                    continue
                if (
                    playback_end is not None
                    and time.monotonic() - playback_end < _ECHO_COOLDOWN_SECONDS
                ):
                    continue
            elif active and _audio_energy(chunk) < _NOISE_GATE_THRESHOLD:
                continue
            await self.model.send_audio(chunk)

    async def _handle_event(self, event: RealtimeModelEvent) -> AsyncIterator[RealtimeEvent]:
        """分发一个模型事件,产出要透给消费方的 RealtimeEvent。"""
        if isinstance(event, ResponseStarted):
            self._is_responding = True
            self._listening = False
            self._response_pending = False
            self._audio_suppressed = False
            self._current_response_id = event.response_id
            self._pending_calls.clear()  # 防御:上一响应未闭合时不让旧调用泄入
        elif isinstance(event, AudioDelta):
            if not self._audio_suppressed:
                self.speaker.play(event.pcm)
        elif isinstance(event, SpeechStarted):
            # 进入"在听":直到本轮 ResponseStarted(开始答)或 TurnDiscarded
            # (判非轮次)为止,期间不注入后台指令。
            self._listening = True
            # 打断:立即停播,并取消进行中的回复;残余 AudioDelta 抑制到
            # 下一个 ResponseStarted。
            self.speaker.clear()
            if self._is_responding:
                await self.model.cancel_response()
                self._audio_suppressed = True
                self._is_responding = False
                self._pending_calls.clear()
                self._audit("interrupted", {"response_id": self._current_response_id})
                yield Interrupted(response_id=self._current_response_id)
                self._current_response_id = None
        elif isinstance(event, UserTranscript):
            self._audit("user_transcript", {"text": event.text})
            yield event
        elif isinstance(event, TurnDiscarded):
            # 判非轮次:这段语音不会触发回答,"在听"结束,回到空闲后补发
            # 排队的后台指令。
            self._listening = False
            await self._flush_pending_texts()
        elif isinstance(event, AssistantTranscript):
            self._audit("assistant_transcript", {"text": event.text})
            yield event
        elif isinstance(event, FunctionCall):
            # 一次响应可含多个 FunctionCall:先收集,待 ResponseDone 后统一
            # 执行,保证全部结果回写完只触发一次二轮推理。
            self._pending_calls.append(event)
        elif isinstance(event, ResponseDone):
            self._is_responding = False
            self._current_response_id = None
            calls, self._pending_calls = self._pending_calls, []
            if calls and not event.cancelled:
                async for out in self._run_calls(calls):
                    yield out
            else:
                # 回合真正结束(没有待执行工具的二轮)才补发排队的后台指令;
                # 若打断后仍"在听",flush 会按兵不动,留到下一次空闲。
                await self._flush_pending_texts()
        elif isinstance(event, ErrorEvent):
            # 非致命服务端错误只记审计不断流(致命错误随后表现为连接关闭)。
            self._audit("error", {"type": event.type, "error": event.message})
            # 失败保底:若被拒的是我们的响应触发,ResponseStarted 永远不会
            # 来,清掉待建标记以免排队指令卡死。
            self._response_pending = False

    async def _run_calls(self, calls: list[FunctionCall]) -> AsyncIterator[RealtimeEvent]:
        """顺序执行收集到的 FunctionCall,逐个回写结果,最后触发二轮推理。"""
        for call in calls:
            self._audit(
                "tool_call", {"id": call.call_id, "name": call.name, "input": call.arguments}
            )
            yield ToolCall(id=call.call_id, name=call.name, input=call.arguments)
            content, is_error = await self._execute(call.name, call.arguments)
            self._audit(
                "tool_result",
                {"id": call.call_id, "name": call.name, "content": content, "is_error": is_error},
            )
            yield ToolResult(id=call.call_id, name=call.name, content=content, is_error=is_error)
            await self.model.send_tool_result(call.call_id, content)
        await self._request_response()

    async def _execute(self, name: str, arguments: dict) -> tuple[str, bool]:
        """执行单个工具调用;语义对齐 ``Agent``:任何失败转错误文本。"""
        tool = self.tools.get(name)
        if tool is None:
            return f"Error: unknown tool {name}", True
        try:
            return await asyncio.to_thread(tool.execute, arguments), False
        except Exception as exc:  # 工具任意异常都不允许打断会话
            return f"Error: {exc}", True

    def _audit(self, kind: str, data: dict) -> None:
        if self.audit is not None:
            self.audit.write(kind, data)
