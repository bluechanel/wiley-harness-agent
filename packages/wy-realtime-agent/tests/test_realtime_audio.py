"""Tests for streaming audio IO, exercised over injected fake device streams."""

import time

from wy_realtime_agent.audio import (
    INPUT_CHUNK_BYTES,
    OUTPUT_CHUNK_BYTES,
    MicSource,
    SpeakerSink,
)


class FakeInputStream:
    def __init__(self, chunks=()) -> None:
        self._chunks = list(chunks)
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def read(self, frames: int):
        if self._chunks:
            return self._chunks.pop(0), False
        time.sleep(0.002)  # 模拟设备阻塞节拍
        return b"\x00" * (frames * 2), False

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class FakeOutputStream:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def write(self, data) -> None:
        self.written.append(bytes(data))

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("条件等待超时")
        time.sleep(0.002)


def test_mic_source_reads_device_chunks_and_stops_cleanly() -> None:
    chunk = b"\x01\x02" * (INPUT_CHUNK_BYTES // 2)
    stream = FakeInputStream([chunk])
    mic = MicSource(stream_factory=lambda: stream)

    mic.start()
    try:
        assert mic.read(timeout=2.0) == chunk
    finally:
        mic.stop()

    assert stream.started and stream.stopped and stream.closed
    mic.stop()  # 幂等


def test_mic_read_times_out_to_none_when_not_started() -> None:
    mic = MicSource(stream_factory=FakeInputStream)

    assert mic.read(timeout=0.01) is None


def test_speaker_sink_chunks_and_writes_in_order() -> None:
    stream = FakeOutputStream()
    sink = SpeakerSink(stream_factory=lambda: stream)
    pcm = bytes(range(256)) * 42  # 10752B → 4800 + 4800 + 1152

    sink.start()
    try:
        sink.play(pcm)
        _wait_until(lambda: not sink.is_playing())
    finally:
        sink.stop()

    assert b"".join(stream.written) == pcm
    assert [len(chunk) for chunk in stream.written[:2]] == [OUTPUT_CHUNK_BYTES] * 2
    assert stream.started and stream.stopped and stream.closed
    sink.stop()  # 幂等


def test_speaker_clear_drops_queued_audio() -> None:
    sink = SpeakerSink(stream_factory=FakeOutputStream)  # 不 start:只验证队列语义

    sink.play(b"\x00" * (OUTPUT_CHUNK_BYTES * 3))
    assert sink.is_playing()

    sink.clear()
    assert not sink.is_playing()
