"""流式音频 IO:麦克风采集与扬声器播放。

音频规格由协议决定:输入 16kHz、输出 24kHz,均为 16bit 单声道 PCM,按
100ms 分块。sounddevice(PortAudio)只在缺省流工厂里懒加载,测试注入假
流即完全不触碰音频设备;PortAudio 流的全部调用都发生在各自的后台线程内,
避免跨线程操作设备句柄。
"""

from __future__ import annotations

import contextlib
import queue
import threading

INPUT_RATE = 16_000  # 协议要求:输入 16kHz 16bit 单声道 PCM
OUTPUT_RATE = 24_000  # 协议要求:输出 24kHz 16bit 单声道 PCM
CHUNK_MS = 100  # 推荐分片:100ms 一块,兼顾实时性与请求频率
SAMPLE_WIDTH = 2  # int16

INPUT_CHUNK_FRAMES = INPUT_RATE * CHUNK_MS // 1000  # 1600
INPUT_CHUNK_BYTES = INPUT_CHUNK_FRAMES * SAMPLE_WIDTH  # 3200
OUTPUT_CHUNK_BYTES = OUTPUT_RATE * CHUNK_MS // 1000 * SAMPLE_WIDTH  # 4800

_JOIN_TIMEOUT_SECONDS = 2.0


def _default_input_stream():
    import sounddevice

    return sounddevice.RawInputStream(
        samplerate=INPUT_RATE,
        blocksize=INPUT_CHUNK_FRAMES,
        channels=1,
        dtype="int16",
    )


def _default_output_stream():
    import sounddevice

    return sounddevice.RawOutputStream(
        samplerate=OUTPUT_RATE, channels=1, dtype="int16"
    )


class MicSource:
    """麦克风流式采集:后台线程阻塞读设备,read() 从队列取 100ms/3200B 一块。

    队列满(消费端停摆)时丢最旧块保实时性;stop() 先收线程再关流,
    保证设备句柄只被采集线程触碰。
    """

    def __init__(self, *, stream_factory=None) -> None:
        self._factory = stream_factory if stream_factory is not None else _default_input_stream
        self._stream = None
        self._thread: threading.Thread | None = None
        self._chunks: queue.Queue[bytes] = queue.Queue(maxsize=50)  # ~5s 背压上限
        self._running = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stream = self._factory()
        self._stream.start()
        self._running = True
        self._thread = threading.Thread(
            target=self._reader_loop, name="mic-source", daemon=True
        )
        self._thread.start()

    def _reader_loop(self) -> None:
        while self._running:
            data, _overflowed = self._stream.read(INPUT_CHUNK_FRAMES)
            chunk = bytes(data)
            try:
                self._chunks.put_nowait(chunk)
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    self._chunks.get_nowait()
                with contextlib.suppress(queue.Full):
                    self._chunks.put_nowait(chunk)

    def read(self, timeout: float = 1.0) -> bytes | None:
        """取一块 PCM;超时返回 None,供调用方检查退出条件后重试。"""
        try:
            return self._chunks.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self) -> None:
        if self._thread is None:
            return
        self._running = False
        self._thread.join(_JOIN_TIMEOUT_SECONDS)
        self._thread = None
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()


class SpeakerSink:
    """扬声器流式播放:play() 切块入队,后台线程顺序写设备。

    play() 先按 100ms 切块再入队,clear() 打断时只需清队列,残余播放
    延迟不超过一块;is_playing() 供回声抑制判断。
    """

    def __init__(self, *, stream_factory=None) -> None:
        self._factory = stream_factory if stream_factory is not None else _default_output_stream
        self._stream = None
        self._thread: threading.Thread | None = None
        self._chunks: queue.Queue[bytes] = queue.Queue()
        self._running = False
        self._writing = False

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stream = self._factory()
        self._stream.start()
        self._running = True
        self._thread = threading.Thread(
            target=self._writer_loop, name="speaker-sink", daemon=True
        )
        self._thread.start()

    def _writer_loop(self) -> None:
        while self._running:
            try:
                chunk = self._chunks.get(timeout=0.1)
            except queue.Empty:
                self._writing = False
                continue
            self._writing = True
            self._stream.write(chunk)
        self._writing = False

    def play(self, pcm: bytes) -> None:
        """入队一段 24kHz int16 单声道 PCM(任意长度,内部切块)。"""
        for start in range(0, len(pcm), OUTPUT_CHUNK_BYTES):
            self._chunks.put(pcm[start : start + OUTPUT_CHUNK_BYTES])

    def clear(self) -> None:
        """打断:丢弃全部未播块。"""
        with self._chunks.mutex:
            self._chunks.queue.clear()

    def is_playing(self) -> bool:
        return self._writing or not self._chunks.empty()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._running = False
        self._thread.join(_JOIN_TIMEOUT_SECONDS)
        self._thread = None
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
