"""Qwen-Audio realtime WebSocket 协议客户端:纯传输层。

只负责建连、客户端事件编码发送与服务端事件解码迭代,不含音频、工具与
对话策略(那些在 ``wy_core.RealtimeAgent`` 与本包 ``qwen``)。连接工厂
可注入,测试用假 ws 对象(只需 ``send``/``close``/async 迭代)即可全链
路验证。传输失败统一抛 ``wy_core.RealtimeError``(本模块 re-export)。
"""

from __future__ import annotations

import base64
import itertools
import json
from collections.abc import AsyncIterator, Sequence

import websockets

from wy_core import RealtimeError, Tool

from wy_realtime_agent.config import RealtimeConfig

__all__ = ["RealtimeClient", "RealtimeError", "build_session_config"]


def build_session_config(
    config: RealtimeConfig, tools: Sequence[Tool], *, system: str | None = None
) -> dict:
    """把 RealtimeConfig、system 指令与工具集组装为 session.update 的 session 载荷。

    voice 与 turn_detection 按协议只在首次 session.update 生效;本客户端
    建连后仅发送一次会话配置,天然满足该约束。
    """
    session: dict = {
        "modalities": ["text", "audio"],
        "voice": config.voice,
        "input_audio_format": "pcm",
        "output_audio_format": "pcm",
        "max_history_turns": config.max_history_turns,
    }
    if system:
        session["instructions"] = system
    if config.mode == "server_vad":
        session["turn_detection"] = {
            "type": "server_vad",
            "threshold": config.vad_threshold,
            "silence_duration_ms": config.vad_silence_ms,
        }
    else:
        session["turn_detection"] = {"type": "smart_turn"}
    if tools:
        session["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in tools
        ]
    return session


async def _default_connect(url: str, headers: dict[str, str]):
    return await websockets.connect(url, additional_headers=headers, max_size=None)


class RealtimeClient:
    """一条实时会话连接:send_* 发客户端事件,events() 迭代服务端事件。"""

    def __init__(self, url: str, api_key: str, model: str, *, connect=None) -> None:
        self.url = url
        self.api_key = api_key
        self.model = model
        self._connect = connect if connect is not None else _default_connect
        self._ws = None
        self._event_ids = itertools.count(1)

    async def connect(self) -> None:
        """建立 WebSocket 连接;失败抛 RealtimeError。"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "x-dashscope-dataInspection": "disable",
        }
        try:
            self._ws = await self._connect(f"{self.url}?model={self.model}", headers)
        except Exception as exc:
            raise RealtimeError(f"WebSocket 连接失败：{exc}") from exc

    async def close(self) -> None:
        if self._ws is not None:
            ws, self._ws = self._ws, None
            await ws.close()

    async def send_event(self, event: dict) -> None:
        """补上 event_id 后发送一个客户端事件;连接已断抛 RealtimeError。"""
        if self._ws is None:
            raise RealtimeError("RealtimeClient 尚未连接")
        payload = {**event, "event_id": f"event_{next(self._event_ids)}"}
        try:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except (websockets.exceptions.WebSocketException, OSError) as exc:
            raise RealtimeError(f"WebSocket 发送失败：{exc}") from exc

    async def update_session(self, session: dict) -> None:
        await self.send_event({"type": "session.update", "session": session})

    async def append_audio(self, pcm: bytes) -> None:
        """流式推送一块 16kHz int16 单声道 PCM。"""
        await self.send_event(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }
        )

    async def cancel_response(self) -> None:
        await self.send_event({"type": "response.cancel"})

    async def create_response(self) -> None:
        await self.send_event({"type": "response.create"})

    async def create_item(self, item: dict) -> None:
        await self.send_event({"type": "conversation.item.create", "item": item})

    async def events(self) -> AsyncIterator[dict]:
        """逐个产出服务端事件;正常关闭自然结束,异常关闭抛 RealtimeError。"""
        if self._ws is None:
            raise RealtimeError("RealtimeClient 尚未连接")
        try:
            async for message in self._ws:
                yield json.loads(message)
        except websockets.exceptions.ConnectionClosed as exc:
            raise RealtimeError(f"WebSocket 连接异常关闭：{exc}") from exc
