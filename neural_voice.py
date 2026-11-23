"""Женский многоязычный голос Эмма; звук существует только в памяти."""

import asyncio
from io import BytesIO
import os
import time
from typing import Callable

VOICE = "en-US-EmmaMultilingualNeural"


async def synthesize(text: str, rate: int, cancelled: Callable) -> bytes:
    """Получить MP3 от сервиса Edge с общим таймаутом у вызывающего кода."""
    import edge_tts

    percent = round((rate / 175 - 1) * 100)
    communicator = edge_tts.Communicate(
        text, VOICE, rate=f"{percent:+d}%", connect_timeout=3,
        receive_timeout=5,
    )
    audio = bytearray()
    async for chunk in communicator.stream():
        if cancelled():
            return b""
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
    if not audio:
        raise OSError("Сервис не вернул звук")
    return bytes(audio)


def speak(text: str, rate: int, volume: int, cancelled: Callable) -> bool:
    """Озвучить MP3 из памяти; остановить воспроизведение по отмене."""
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    import pygame.mixer as mixer

    async def request() -> bytes:
        task = asyncio.create_task(asyncio.wait_for(synthesize(text, rate, cancelled), 6))
        try:
            while not task.done():
                if cancelled():
                    return b""
                await asyncio.wait({task}, timeout=0.10)
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    audio = asyncio.run(request())
    if cancelled() or not audio:
        return False
    if mixer.get_init() is None:
        mixer.init()
    try:
        with BytesIO(audio) as stream:
            mixer.music.load(stream, "mp3")
            mixer.music.set_volume(volume / 100)
            mixer.music.play()
            while mixer.music.get_busy():
                if cancelled():
                    return False
                time.sleep(0.04)
        return True
    finally:
        mixer.music.stop()
        mixer.music.unload()


def close() -> None:
    """Освободить аудиоустройство при завершении приложения."""
    import pygame.mixer as mixer

    mixer.quit()
