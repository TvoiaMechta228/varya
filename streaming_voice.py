"""Озвучивание законченных фраз параллельно генерации локального ответа."""

from contextvars import copy_context
from queue import Queue
import re
import threading
import time

import settings
import speech


class StreamingVoice:
    """Одна очередь фраз на один ответ с независимой отменой воспроизведения."""

    def __init__(self) -> None:
        self.queue = Queue()
        self.buffer = ""
        self.cancelled = threading.Event()
        self.success = True
        self.closed = False
        context = copy_context()
        self.thread = threading.Thread(
            target=lambda: context.run(self._run),
            daemon=True,
            name="VaryaStreamingVoice",
        )
        self.thread.start()

    def feed(self, token: str) -> None:
        if self.closed or self.cancelled.is_set():
            return
        self.buffer += token
        while self.buffer:
            boundary = re.search(r"[.!?](?:\s|$)|\n", self.buffer)
            if boundary and boundary.end() >= 12:
                end = boundary.end()
            elif len(self.buffer) > 220:
                end = self.buffer.rfind(" ", 100, 220)
                if end < 0:
                    end = 220
            else:
                break
            text, self.buffer = self.buffer[:end].strip(), self.buffer[end:]
            if text:
                self.queue.put(text)

    def _run(self) -> None:
        import offline_speech

        token = speech.audio_cancel.set(self.cancelled)
        try:
            while not self.cancelled.is_set() and not speech.is_cancelled():
                text = self.queue.get()
                if text is None:
                    return
                options = settings.snapshot()
                if not offline_speech.speak(text, options["rate"], options["volume"]):
                    self.success = False
                    return
        except Exception:
            self.success = False
        finally:
            speech.audio_cancel.reset(token)

    def finish(self, *, abort: bool = False) -> None:
        if self.closed:
            return
        self.closed = True
        if abort:
            self.cancelled.set()
        elif self.buffer.strip():
            self.queue.put(self.buffer.strip())
        self.queue.put(None)
        deadline = time.monotonic() + 180
        while self.thread.is_alive() and time.monotonic() < deadline:
            if speech.is_cancelled():
                self.cancelled.set()
            self.thread.join(timeout=0.1)
        if self.thread.is_alive():
            self.cancelled.set()
            self.success = False
