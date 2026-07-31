"""Qwen-Audio realtime 的 RealtimeModel 实现:wire 事件与类型化事件的翻译层。

组合 ``RealtimeClient``(纯传输)实现 ``wy_core.RealtimeModel`` 契约:
发送侧把契约方法编码为 Qwen wire 事件;接收侧把 wire dict 翻译为 wy-core
的类型化事件,词汇之外的 wire 事件(含 voiceprint 注册、session.created
等)静默忽略。厂商参数经 ``RealtimeConfig`` 构造期注入。
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Sequence

from wy_core import (
    AssistantTranscript,
    AssistantTranscriptDelta,
    AudioDelta,
    ErrorEvent,
    FunctionCall,
    RealtimeModel,
    RealtimeModelEvent,
    ResponseDone,
    ResponseStarted,
    SessionReady,
    SpeechStarted,
    SpeechStopped,
    Tool,
    TurnCommitted,
    TurnDiscarded,
    UserTranscript,
    UserTranscriptDelta,
)

from wy_realtime_agent.config import RealtimeConfig
from wy_realtime_agent.protocol import RealtimeClient, build_session_config


class QwenRealtimeModel(RealtimeModel):
    """Qwen-Audio realtime WebSocket 协议的实时模型实现。

    ``client`` 可注入替身(测试或自定义传输),缺省按 config 构造
    ``RealtimeClient``;传输失败由 client 统一抛 ``RealtimeError``。
    """

    def __init__(self, config: RealtimeConfig, *, client: RealtimeClient | None = None) -> None:
        self.config = config
        self.name = config.model
        self.client = (
            client
            if client is not None
            else RealtimeClient(config.url, config.api_key, config.model)
        )

    async def connect(self) -> None:
        await self.client.connect()

    async def update_session(
        self, *, system: str | None = None, tools: Sequence[Tool] = ()
    ) -> dict:
        session = build_session_config(self.config, tools, system=system)
        await self.client.update_session(session)
        return session

    async def send_audio(self, pcm: bytes) -> None:
        await self.client.append_audio(pcm)

    async def send_user_text(self, text: str) -> None:
        await self.client.create_item(
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": text}],
            }
        )

    async def send_tool_result(self, call_id: str, output: str) -> None:
        await self.client.create_item(
            {"type": "function_call_output", "call_id": call_id, "output": output}
        )

    async def create_response(self) -> None:
        await self.client.create_response()

    async def cancel_response(self) -> None:
        await self.client.cancel_response()

    async def events(self) -> AsyncIterator[RealtimeModelEvent]:
        async for event in self.client.events():
            translated = _translate(event)
            if translated is not None:
                yield translated

    async def close(self) -> None:
        await self.client.close()


def _translate(event: dict) -> RealtimeModelEvent | None:
    """一个 Qwen wire 事件 → 一个类型化事件;词汇之外返回 None。"""
    event_type = event.get("type")
    if event_type == "session.updated":
        # 服务端确认 session.update 生效,会话就绪(session.created 不透出)。
        return SessionReady()
    if event_type == "response.created":
        return ResponseStarted(response_id=event.get("response", {}).get("id"))
    if event_type == "response.audio.delta":
        return AudioDelta(pcm=base64.b64decode(event.get("delta", "")))
    if event_type == "input_audio_buffer.speech_started":
        return SpeechStarted()
    if event_type == "input_audio_buffer.speech_stopped":
        # smart_turn 判无效轮时带 reason="turn_invalid",有效轮无此字段。
        return SpeechStopped(reason=event.get("reason"))
    if event_type == "input_audio_buffer.committed":
        return TurnCommitted()
    if event_type == "conversation.item.input_audio_transcription.delta":
        # ASR 流式增量:text 为新确定的文本,stash 为可被修订的暂存尾部。
        return UserTranscriptDelta(text=event.get("text", ""), stash=event.get("stash", ""))
    if event_type == "conversation.item.input_audio_transcription.completed":
        return UserTranscript(text=event.get("transcript", ""))
    if event_type == "conversation.item.input_audio_transcription.failed":
        error = event.get("error", {})
        return ErrorEvent(
            type=error.get("type", "transcription_error"), message=error.get("message", "")
        )
    if event_type == "conversation.item.ambient_audio_transcription.completed":
        # smart_turn 判定为非有效轮次;ambient 转写本身不透出、不入上下文。
        return TurnDiscarded()
    if event_type == "response.audio_transcript.delta":
        return AssistantTranscriptDelta(text=event.get("delta", ""))
    if event_type == "response.audio_transcript.done":
        return AssistantTranscript(text=event.get("transcript", ""))
    if event_type == "response.text.delta":
        # 纯文本模态(modalities=["text"])的输出增量,与字幕增量同词汇。
        return AssistantTranscriptDelta(text=event.get("delta", ""))
    if event_type == "response.text.done":
        return AssistantTranscript(text=event.get("text", ""))
    if event_type == "response.function_call_arguments.done":
        return FunctionCall(
            call_id=event.get("call_id", ""),
            name=event.get("name", ""),
            arguments=_parse_arguments(event.get("arguments")),
        )
    if event_type == "response.done":
        return ResponseDone(cancelled=event.get("response", {}).get("status") == "cancelled")
    if event_type == "error":
        error = event.get("error", {})
        return ErrorEvent(type=error.get("type", ""), message=error.get("message", ""))
    return None


def _parse_arguments(raw: object) -> dict:
    """模型给出的入参 JSON 字符串 → dict;残缺或非对象一律回退空参。"""
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return arguments if isinstance(arguments, dict) else {}
