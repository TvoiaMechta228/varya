"""Распознавание речи без файлов и последовательный синтез SAPI5."""

from collections.abc import Callable
from contextvars import ContextVar
import logging
import sys
import threading
import time
from typing import Any

import speech_recognition as sr

import config
import settings

logger = logging.getLogger(__name__)
_recognizer: sr.Recognizer | None = None
_engine: Any = None
cancel_event: threading.Event | None = None
shutdown_event: threading.Event | None = None
_neural_retry_at = 0.0
_sapi_preference = None
_calibrated = False
_last_service_error = float("-inf")
quiet_reply: ContextVar[bool] = ContextVar("quiet_reply", default=False)
streaming_reply: ContextVar[Any] = ContextVar("streaming_reply", default=None)
audio_cancel: ContextVar[threading.Event | None] = ContextVar("audio_cancel", default=None)
barge_in_enabled = False
barge_in_callback: Callable[[], None] | None = None
transcript: ContextVar[Callable[[str, str], None] | None] = ContextVar(
    "transcript", default=None,
)


def publish(role: str, text: str) -> None:
    """Передать реплику интерфейсу речевого потока."""
    stream = streaming_reply.get()
    if role == "delta" and stream is not None:
        stream.feed(text)
    listener = transcript.get()
    if listener is not None:
        listener(role, text)
    elif role == "private_text" and sys.stdout is not None:
        print(text, flush=True)


def new_session() -> None:
    """Обновить оценку фонового шума при следующем включении микрофона."""
    global _calibrated
    _calibrated = False


def is_cancelled() -> bool:
    """Проверить остановку с кнопки интерфейса."""
    return (
        audio_cancel.get() is not None and audio_cancel.get().is_set()
        or
        cancel_event is not None and cancel_event.is_set()
        or shutdown_event is not None and shutdown_event.is_set()
    )


def close() -> None:
    """Освободить движок в том же потоке, где он был создан."""
    global _engine
    if _engine is not None:
        _engine.stop()
        _engine = None
    if "neural_voice" in sys.modules:
        sys.modules["neural_voice"].close()


def _get_recognizer() -> sr.Recognizer:
    """Создать распознаватель с ограничением сетевого ожидания."""
    global _recognizer
    if _recognizer is None:
        _recognizer = sr.Recognizer()
        _recognizer.operation_timeout = config.NETWORK_TIMEOUT
    return _recognizer


def windows_voices() -> dict[str, str]:
    """Прочитать установленные голоса SAPI из реестра без открытия аудиоустройства."""
    import winreg

    voices = {}
    branch = r"SOFTWARE\Microsoft\Speech\Voices\Tokens"
    for hive, prefix in ((winreg.HKEY_LOCAL_MACHINE, "HKEY_LOCAL_MACHINE"),
                         (winreg.HKEY_CURRENT_USER, "HKEY_CURRENT_USER")):
        try:
            with winreg.OpenKey(hive, branch) as tokens:
                for i in range(winreg.QueryInfoKey(tokens)[0]):
                    name = winreg.EnumKey(tokens, i)
                    with winreg.OpenKey(tokens, name) as token:
                        title = winreg.QueryValue(token, None)
                        voices[title] = "\\".join((prefix, branch, name))
        except OSError:
            continue
    return voices


def _voice_description(voice: Any) -> str:
    """Собрать метаданные голоса, включая языки в байтовой форме."""
    languages = [
        item.decode("utf-8", errors="ignore")
        if isinstance(item, bytes) else str(item)
        for item in (getattr(voice, "languages", None) or [])
    ]
    return " ".join([
        str(voice.id), str(getattr(voice, "name", "")), *languages,
    ]).lower()


def speak(text: str) -> bool:
    """Озвучить текст; при сбое вывести его в консоль и вернуть False."""
    global _engine, _neural_retry_at, _sapi_preference
    publish("assistant", text)
    if settings.snapshot()["save_history"]:
        logger.info("Ответ: %s", text)
    if sys.stdout is not None and sys.stdout is not sys.stderr:
        print(f"{config.ASSISTANT_NAME}: {text}", flush=True)
    options = settings.snapshot()
    if quiet_reply.get():
        return not is_cancelled()
    publish("state", "Озвучиваю ответ…")
    if options["voice"] == "piper":
        try:
            import offline_speech
            return offline_speech.speak(text, options["rate"], options["volume"])
        except Exception:
            logger.exception("Локальный голос Piper недоступен")
            publish("notice", "Голос Piper недоступен. Использую голос Windows.")
    if options["voice"] == "neural" and time.monotonic() >= _neural_retry_at:
        try:
            import neural_voice

            return neural_voice.speak(
                text, options["rate"], options["volume"], is_cancelled,
            )
        except Exception:
            logger.warning("Нейроголос недоступен; использую голос Windows",
                           exc_info=True)
            _neural_retry_at = time.monotonic() + 60
            publish("notice", "Сетевой голос недоступен. Использую голос Windows.")
    if is_cancelled():
        return False
    try:
        preference = options.get("sapi_voice", "")
        if _engine is None or (_sapi_preference is not None and preference != _sapi_preference):
            import pyttsx3

            _engine = pyttsx3.init(driverName="sapi5")
            _engine.setProperty("rate", config.SPEECH_RATE)
            _sapi_preference = preference
            voices = _engine.getProperty("voices")
            selected = [v for v in voices if v.id == preference]
            for voice in selected or voices:
                description = _voice_description(voice)
                if selected or any(tag in description for tag in (
                    "ru-ru", "ru_ru", "russian", "рус", "irina", "0419",
                )):
                    _engine.setProperty("voice", voice.id)
                    break
            else:
                logger.warning("Русский голос не найден; выбран системный")
        _engine.setProperty("rate", options["rate"])
        _engine.setProperty("volume", options["volume"] / 100)
        _engine.say(text)
        if cancel_event is None:
            _engine.runAndWait()
        else:
            _engine.startLoop(False)
            try:
                _engine.iterate()
                while _engine.isBusy():
                    if is_cancelled():
                        _engine.stop()
                        return False
                    _engine.iterate()
                    time.sleep(0.02)
            finally:
                _engine.endLoop()
        return True
    except Exception:
        # COM-драйверы выбрасывают собственные типы исключений.
        logger.exception("Ошибка синтеза речи")
        _engine = None
        return False


class MeterStream:
    """Передавать индикатору только уровень сигнала во время чтения микрофона."""

    def __init__(self, stream):
        self.stream = stream
        self.last = 0.0

    def read(self, frames):
        if is_cancelled():
            raise InterruptedError("Прослушивание остановлено")
        chunk = self.stream.read(frames)
        now = time.monotonic()
        if chunk and now - self.last > .08:
            samples = memoryview(chunk).cast("h")[::8]
            rms = (sum(v*v for v in samples) / max(1,len(samples))) ** .5
            publish("level", str(min(1.0, rms / 4500)))
            self.last = now
        return chunk

    def close(self):
        self.stream.close()


def listen(
    *, timeout: float = config.LISTEN_TIMEOUT,
    phrase_time_limit: float = config.PHRASE_TIME_LIMIT,
) -> str | None:
    """Слушать одну реплику; при тишине или ошибке вернуть None."""
    global _calibrated, _last_service_error
    try:
        if is_cancelled():
            return None
        if settings.snapshot().get("recognition") == "vosk":
            import offline_speech
            return offline_speech.listen(timeout, phrase_time_limit)
        recognizer = _get_recognizer()
        with sr.Microphone(device_index=settings.snapshot().get("microphone", config.MICROPHONE_INDEX)) as source:
            if not _calibrated:
                publish("state", "Настраиваю микрофон…")
                recognizer.adjust_for_ambient_noise(source, duration=config.AMBIENT_DURATION)
                _calibrated = True
            if transcript.get() is not None and source.SAMPLE_WIDTH == 2:
                source.stream = MeterStream(source.stream)
            publish("state", "Слушаю…")
            audio = recognizer.listen(
                source, timeout=timeout, phrase_time_limit=phrase_time_limit,
            )
        if is_cancelled():
            return None
        text = recognizer.recognize_google(audio, language=config.LANGUAGE)
        if is_cancelled():
            return None
        if settings.snapshot()["save_history"]:
            logger.info("Распознано: %s", text)
        return text
    except sr.WaitTimeoutError:
        logger.debug("Таймаут ожидания речи")
    except sr.UnknownValueError:
        logger.info("Речь не разобрана")
        publish("notice", "Не расслышала. Повтори просьбу или напиши её внизу окна.")
    except sr.RequestError:
        logger.warning("Сервис распознавания недоступен", exc_info=True)
        if time.monotonic() - _last_service_error >= 30:
            _last_service_error = time.monotonic()
            speak("Сервис распознавания недоступен. Можно написать сообщение внизу окна.")
        time.sleep(config.ERROR_RETRY_DELAY)
    except InterruptedError:
        return None
    except (OSError, AttributeError, ValueError, AssertionError):
        new_session()
        publish("mic_unavailable", "")
        logger.exception("Микрофон недоступен или PyAudio не установлен")
        speak("Не удалось открыть микрофон. Напишите сообщение в поле ввода.")
        time.sleep(config.ERROR_RETRY_DELAY)
    except Exception:
        logger.exception("Непредвиденная ошибка распознавания")
        speak("Ошибка распознавания. Попробуйте ещё раз.")
        time.sleep(config.ERROR_RETRY_DELAY)
    return None
