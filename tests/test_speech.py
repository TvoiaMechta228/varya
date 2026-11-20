"""Проверки микрофона без реальной записи и сетевых запросов."""

from unittest.mock import Mock, MagicMock
from types import SimpleNamespace
import sys

import pytest
import speech_recognition as sr

import config
import speech

_REAL_SPEAK = speech.speak


@pytest.fixture
def microphone(monkeypatch):
    """Создать поддельные микрофон и распознаватель."""
    recognizer = Mock()
    monkeypatch.setattr(speech, "_recognizer", recognizer)
    monkeypatch.setattr(speech.sr, "Microphone", MagicMock())
    return recognizer


def test_recognized_text(microphone):
    """Передавать русский язык и ограничение длины записи."""
    microphone.recognize_google.return_value = "который час"
    assert speech.listen() == "который час"
    microphone.recognize_google.assert_called_once_with(
        microphone.listen.return_value, language="ru-RU",
    )
    assert microphone.listen.call_args.kwargs == {
        "timeout": config.LISTEN_TIMEOUT,
        "phrase_time_limit": config.PHRASE_TIME_LIMIT,
    }


@pytest.mark.parametrize("error", [
    sr.WaitTimeoutError(), sr.UnknownValueError(),
])
def test_listen_errors(microphone, error):
    """Тишина и неразборчивая речь не выбрасывают исключения."""
    microphone.listen.side_effect = error
    assert speech.listen() is None


def test_service_error(microphone, mute_speech):
    """Ошибка Google озвучивается и возвращает управление циклу."""
    microphone.recognize_google.side_effect = sr.RequestError("offline")
    assert speech.listen() is None
    assert "Сервис распознавания недоступен" in mute_speech.call_args.args[0]


def test_device_error(monkeypatch, mute_speech):
    """Отсутствие микрофона не роняет приложение."""
    monkeypatch.setattr(speech.sr, "Microphone", Mock(side_effect=OSError))
    assert speech.listen() is None
    assert "микрофон" in mute_speech.call_args.args[0]


def test_russian_voice(monkeypatch):
    """При инициализации выбирать доступный русский SAPI5-голос."""
    engine = Mock()
    engine.getProperty.return_value = [
        SimpleNamespace(id="en", name="English", languages=[b"en-US"]),
        SimpleNamespace(id="ru", name="Russian", languages=[b"ru-RU"]),
    ]
    init = Mock(return_value=engine)
    monkeypatch.setitem(sys.modules, "pyttsx3", SimpleNamespace(init=init))
    monkeypatch.setattr(speech, "_engine", None)
    assert _REAL_SPEAK("Привет")
    init.assert_called_once_with(driverName="sapi5")
    engine.setProperty.assert_any_call("voice", "ru")
    engine.say.assert_called_once_with("Привет")
    engine.runAndWait.assert_called_once_with()


def test_synthesis_error(monkeypatch):
    """Сбой COM не прекращает работу и возвращает признак ошибки."""
    monkeypatch.setattr(speech, "_engine", None)
    monkeypatch.setitem(sys.modules, "pyttsx3", SimpleNamespace(
        init=Mock(side_effect=RuntimeError("COM error")),
    ))
    assert not _REAL_SPEAK("Привет")
    assert speech._engine is None
