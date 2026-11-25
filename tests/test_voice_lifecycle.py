"""Озвучивание начинается до завершения ответа и завершается по отмене."""

import asyncio
import threading
import time

import local_brain
import offline_speech
import speech
from streaming_voice import StreamingVoice


def test_stream_starts_before_response_finishes(monkeypatch):
    spoken = []
    started = threading.Event()

    def say(text, *_):
        spoken.append(text)
        started.set()
        return True

    monkeypatch.setattr(offline_speech, "speak", say)
    voice = StreamingVoice()
    voice.feed("Первая законченная фраза. ")
    assert started.wait(2)
    assert not voice.closed
    voice.feed("Остаток ответа")
    voice.finish()
    assert voice.success and not voice.thread.is_alive()
    assert spoken == ["Первая законченная фраза.", "Остаток ответа"]


def test_stream_abort_interrupts_current_audio(monkeypatch):
    started = threading.Event()

    def say(*_):
        started.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not speech.is_cancelled():
            time.sleep(0.01)
        return False

    monkeypatch.setattr(offline_speech, "speak", say)
    voice = StreamingVoice()
    voice.feed("Достаточно длинная фраза. ")
    assert started.wait(2)
    start = time.monotonic()
    voice.finish(abort=True)
    assert time.monotonic() - start < 1
    assert not voice.thread.is_alive()


def test_model_cancel_does_not_wait_for_first_token(monkeypatch):
    cancel = threading.Event()
    monkeypatch.setattr(speech, "cancel_event", cancel)

    async def stalled(*args, **kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(local_brain, "_complete", stalled)
    timer = threading.Timer(0.1, cancel.set)
    timer.start()
    import pytest

    start = time.monotonic()
    try:
        with pytest.raises(InterruptedError):
            asyncio.run(local_brain.complete([]))
    finally:
        timer.join()
    assert time.monotonic() - start < 1
