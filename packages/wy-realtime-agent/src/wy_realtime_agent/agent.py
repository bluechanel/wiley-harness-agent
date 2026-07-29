"""RealtimeAgent 编排:协议客户端 + 流式音频 + 工具执行 + 审计。

与 wy_core.Agent 的关系:实时协议是服务端维护上下文、VAD 主动触发响应的
推送式全双工流,不适配 ``Model.stream``/``Agent.run`` 的拉取式回合契约,
故本模块自成循环;但工具执行语义与事件词汇对齐 wy-core——工具按
``wy_core.Tool`` 契约编写,经 ``asyncio.to_thread`` 同步执行,失败转
``Error: ...`` 文本不中断会话,工具事件复用 ``wy_core.ToolCall``/
``ToolResult``,审计走 ``wy_core.AuditLog``(语义级事件,不逐 delta 留痕)。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import cast

from wy_core import AuditLog, Tool, ToolCall, ToolResult

from wy_realtime_agent.audio import MicSource, SpeakerSink
from wy_realtime_agent.config import RealtimeConfig
from wy_realtime_agent.protocol import (
    RealtimeClient,
    RealtimeError,
    build_session_config,
)


@dataclass
class UserTranscript:
    """一段用户语音的转写已完成。"""

    text: str


@dataclass
class AssistantTranscript:
    """一次模型语音回复的转写已完成。"""

    text: str


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
    """把 RealtimeClient、音频 IO 与 Tool 组装为完整实时语音 agent。

    审计默认开启:省略 ``audit`` 即写入 CWD/.wy_audit/,显式传 ``audit=None``
    关闭。单个实例不支持并发 ``run``;``run`` 期间可经 ``send_user_text``
    注入后台文字指令(空闲时注入并立即触发响应,忙碌时排队);``close()``
    释放 factory 注入的资源(如 MCP 连接),幂等,宿主 finally 里调用。
    """

    def __init__(
        self,
        *,
        client: RealtimeClient,
        config: RealtimeConfig,
        tools: Sequence[Tool] = (),
        mic: MicSource | None = None,
        speaker: SpeakerSink | None = None,
        audit: AuditLog | None = _DEFAULT_AUDIT,
        closer: Callable[[], None] | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.tools = {t.name: t for t in tools}
        if len(self.tools) != len(tools):
            raise ValueError("工具名重复")
        self.mic = mic if mic is not None else MicSource()
        self.speaker = speaker if speaker is not None else SpeakerSink()
        self.audit = AuditLog.default() if audit is _DEFAULT_AUDIT else audit
        self._closer = closer
        self._is_responding = False
        self._current_response_id: str | None = None
        self._audio_suppressed = False
        self._listening = False
        self._response_pending = False
        self._pending_calls: list[dict] = []
        self._pending_texts: list[str] = []
        self._audit(
            "agent_start",
            {
                "model": config.model,
                "tools": list(self.tools),
                "mode": config.mode,
                "voice": config.voice,
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
            await self.client.connect()
            session = build_session_config(self.config, list(self.tools.values()))
            await self.client.update_session(session)
            self._audit("session_update", {"session": session})

            self.mic.start()
            self.speaker.start()
            sender = asyncio.create_task(self._send_audio(), name="mic-sender")

            async for event in self.client.events():
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
            await self.client.close()

    def close(self) -> None:
        """释放 factory 注入的资源(如 MCP 连接);幂等。"""
        if self._closer is not None:
            closer, self._closer = self._closer, None
            closer()

    async def send_user_text(self, text: str) -> None:
        """注入一条后台文字指令(conversation.item.create,role=user)并让模型立即执行。

        仅在空闲——既不在听(speech_started 起,到该轮 response.created 或
        smart_turn 判非轮次止)也不在答(response.created→response.done)——
        时注入,注入后紧跟一次 response.create 触发模型开始执行;忙碌期间
        先入队,待回合真正结束(无待执行工具的 response.done,或 ambient
        判非轮次)后按序补发,并只触发一次响应。在 ``run()`` 所在事件循环中
        调用;尚未建连时抛 RealtimeError。
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
            await self._send_user_item(text)
        await self._request_response()

    async def _send_user_item(self, text: str) -> None:
        await self.client.create_item(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        )
        self._audit("user_text", {"text": text})

    async def _request_response(self) -> None:
        """发 response.create 并标记响应待建,到 response.created 为止。

        标记期间新指令只入队不再触发,避免连续两次 create 撞出
        already_has_active_response 类错误。
        """
        self._response_pending = True
        await self.client.create_response()

    async def _send_audio(self) -> None:
        """持续把麦克风 PCM 推给服务端,按配置做回声抑制。

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
            if self.config.echo_suppression:
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
            await self.client.append_audio(chunk)

    async def _handle_event(self, event: dict) -> AsyncIterator[RealtimeEvent]:
        """分发一个服务端事件,产出要透给消费方的 RealtimeEvent;未知事件忽略。"""
        event_type = event.get("type")
        if event_type == "response.created":
            self._is_responding = True
            self._listening = False
            self._response_pending = False
            self._audio_suppressed = False
            self._current_response_id = event.get("response", {}).get("id")
            self._pending_calls.clear()  # 防御:上一响应未闭合时不让旧调用泄入
        elif event_type == "response.audio.delta":
            if not self._audio_suppressed:
                self.speaker.play(base64.b64decode(event.get("delta", "")))
        elif event_type == "input_audio_buffer.speech_started":
            # 进入"在听":直到本轮 response.created(开始答)或 smart_turn 判非
            # 轮次(ambient)为止,期间不注入后台指令。
            self._listening = True
            # 打断:立即停播,并取消进行中的回复;残余 audio.delta 抑制到下一个 response.created。
            self.speaker.clear()
            if self._is_responding:
                await self.client.cancel_response()
                self._audio_suppressed = True
                self._is_responding = False
                self._pending_calls.clear()
                self._audit("interrupted", {"response_id": self._current_response_id})
                yield Interrupted(response_id=self._current_response_id)
                self._current_response_id = None
        elif event_type == "conversation.item.input_audio_transcription.completed":
            text = event.get("transcript", "")
            self._audit("user_transcript", {"text": text})
            yield UserTranscript(text=text)
        elif event_type == "conversation.item.ambient_audio_transcription.completed":
            # smart_turn 判定为非有效轮次:这段语音不会触发回答,"在听"结束,
            # 回到空闲后补发排队的后台指令(转写本身不透出、不入上下文)。
            self._listening = False
            await self._flush_pending_texts()
        elif event_type == "response.audio_transcript.done":
            text = event.get("transcript", "")
            self._audit("assistant_transcript", {"text": text})
            yield AssistantTranscript(text=text)
        elif event_type == "response.function_call_arguments.done":
            # 一次响应可含多个 function_call:先收集,待 response.done 后统一执行,
            # 保证全部结果回写完只触发一次二轮推理。
            self._pending_calls.append(event)
        elif event_type == "response.done":
            self._is_responding = False
            self._current_response_id = None
            status = event.get("response", {}).get("status")
            calls, self._pending_calls = self._pending_calls, []
            if calls and status != "cancelled":
                async for out in self._run_calls(calls):
                    yield out
            else:
                # 回合真正结束(没有待执行工具的二轮)才补发排队的后台指令;
                # 若打断后仍"在听",flush 会按兵不动,留到下一次空闲。
                await self._flush_pending_texts()
        elif event_type == "error":
            error = event.get("error", {})
            # 客户端参数类错误不致断连,记审计后继续收流;服务端错误随后会关闭连接。
            self._audit(
                "error",
                {"type": error.get("type", ""), "error": error.get("message", "")},
            )
            # 失败保底:若被拒的是我们的 response.create,response.created 永远
            # 不会来,清掉待建标记以免排队指令卡死。
            self._response_pending = False

    async def _run_calls(self, calls: list[dict]) -> AsyncIterator[RealtimeEvent]:
        """顺序执行收集到的 function_call,逐个回写结果,最后触发二轮推理。"""
        for call in calls:
            call_id = call.get("call_id", "")
            name = call.get("name", "")
            arguments = _parse_arguments(call.get("arguments"))
            self._audit("tool_call", {"id": call_id, "name": name, "input": arguments})
            yield ToolCall(id=call_id, name=name, input=arguments)
            content, is_error = await self._execute(name, arguments)
            self._audit(
                "tool_result",
                {"id": call_id, "name": name, "content": content, "is_error": is_error},
            )
            yield ToolResult(id=call_id, name=name, content=content, is_error=is_error)
            await self.client.create_item(
                {"type": "function_call_output", "call_id": call_id, "output": content}
            )
        await self._request_response()

    async def _execute(self, name: str, arguments: dict) -> tuple[str, bool]:
        """执行单个工具调用;语义对齐 wy_core.Agent:任何失败转错误文本。"""
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


def _parse_arguments(raw: object) -> dict:
    """模型给出的入参 JSON 字符串 → dict;残缺或非对象一律回退空参。"""
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return arguments if isinstance(arguments, dict) else {}
