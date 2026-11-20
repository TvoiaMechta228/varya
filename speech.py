"""Распознавание речи без файлов и последовательный синтез SAPI5."""

import logging
import sys
import threading
import time
from typing import Any

import speech_recognition as sr

import config

logger = logging.getLogger(__name__)
_recognizer: sr.Recognizer | None = None
_engine: Any = None
cancel_event: threading.Event | None = None


def is_cancelled() -> bool:
    """Проверить остановку с кнопки интерфейса."""
    return cancel_event is not None and cancel_event.is_set()


def close() -> None:
    """Освободить движок в том же потоке, где он был создан."""
    global _engine
    if _engine is not None:
        _engine.stop()
        _engine = None


def _get_recognizer() -> sr.Recognizer:
    """Создать распознаватель с ограничением сетевого ожидания."""
    global _recognizer
    if _recognizer is None:
        _recognizer = sr.Recognizer()
        _recognizer.operation_timeout = config.NETWORK_TIMEOUT
    return _recognizer


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
    global _engine
    logger.info("Ответ: %s", text)
    if sys.stdout is not None:
        print(f"{config.ASSISTANT_NAME}: {text}", flush=True)
    try:
        if _engine is None:
            import pyttsx3

            _engine = pyttsx3.init(driverName="sapi5")
            _engine.setProperty("rate", config.SPEECH_RATE)
            for voice in _engine.getProperty("voices"):
                description = _voice_description(voice)
                if any(tag in description for tag in (
                    "ru-ru", "ru_ru", "russian", "рус", "irina", "0419",
                )):
                    _engine.setProperty("voice", voice.id)
                    break
            else:
                logger.warning("Русский голос не найден; выбран системный")
        _engine.say(text)
        _engine.runAndWait()
        return True
    except Exception:
        # COM-драйверы выбрасывают собственные типы исключений.
        logger.exception("Ошибка синтеза речи")
        _engine = None
        return False


def listen(
    *, timeout: float = config.LISTEN_TIMEOUT,
    phrase_time_limit: float = config.PHRASE_TIME_LIMIT,
) -> str | None:
    """Слушать одну реплику; при тишине или ошибке вернуть None."""
    try:
        if is_cancelled():
            return None
        recognizer = _get_recognizer()
        with sr.Microphone(device_index=config.MICROPHONE_INDEX) as source:
            recognizer.adjust_for_ambient_noise(
                source, duration=config.AMBIENT_DURATION,
            )
            audio = recognizer.listen(
                source, timeout=timeout, phrase_time_limit=phrase_time_limit,
            )
        if is_cancelled():
            return None
        text = recognizer.recognize_google(audio, language=config.LANGUAGE)
        if is_cancelled():
            return None
        logger.info("Распознано: %s", text)
        return text
    except sr.WaitTimeoutError:
        logger.debug("Таймаут ожидания речи")
    except sr.UnknownValueError:
        logger.info("Речь не разобрана")
    except sr.RequestError:
        logger.warning("Сервис распознавания недоступен", exc_info=True)
        speak("Сервис распознавания недоступен. Попробуйте ещё раз.")
        time.sleep(config.ERROR_RETRY_DELAY)
    except (OSError, AttributeError):
        logger.exception("Микрофон недоступен или PyAudio не установлен")
        speak("Не удалось открыть микрофон. Проверьте его подключение.")
        time.sleep(config.ERROR_RETRY_DELAY)
    except Exception:
        logger.exception("Непредвиденная ошибка распознавания")
        speak("Ошибка распознавания. Попробуйте ещё раз.")
        time.sleep(config.ERROR_RETRY_DELAY)
    return None
