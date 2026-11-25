"""Проверки отмены команд и запуска без консоли."""

import threading
from queue import Queue
from unittest.mock import Mock

import safety
import speech
from legacy.gui import AssistantController
from test_speech import _REAL_SPEAK


def test_speak_without_console(monkeypatch):
    """Оконная сборка не требует sys.stdout."""
    engine = Mock()
    monkeypatch.setattr(speech, "_engine", engine)
    monkeypatch.setattr(speech.sys, "stdout", None)
    assert _REAL_SPEAK("Проверка")
    engine.say.assert_called_once_with("Проверка")


def test_stopped_microphone_does_not_open(monkeypatch):
    """После остановки микрофон не открывается заново."""
    event = threading.Event()
    event.set()
    microphone = Mock()
    monkeypatch.setattr(speech, "cancel_event", event)
    monkeypatch.setattr(speech.sr, "Microphone", microphone)
    assert speech.listen() is None
    microphone.assert_not_called()


def test_stop_during_confirmation_denies(monkeypatch):
    """Кнопка остановки отменяет даже правильное подтверждение."""
    event = threading.Event()
    monkeypatch.setattr(speech, "cancel_event", event)

    def answer(**kwargs):
        event.set()
        return "да выключение"

    monkeypatch.setattr(speech, "listen", answer)
    assert not safety.confirm("shutdown")


def test_stop_discards_pending_command(monkeypatch):
    """После остановки распознанная реплика не доходит до действий ПК."""
    entered = threading.Event()
    release = threading.Event()
    route = Mock(return_value=True)

    def listen():
        entered.set()
        assert release.wait(5)
        return "открой блокнот"

    monkeypatch.setattr(speech, "listen", listen)
    monkeypatch.setattr(speech, "close", Mock())
    monkeypatch.setattr("main.route_command", route)
    controller = AssistantController(Queue())
    try:
        controller.start()
        assert entered.wait(5)
        controller.stop()
    finally:
        controller.close()
        release.set()
        controller.thread.join(5)
    assert not controller.thread.is_alive()
    assert speech.cancel_event is None
    route.assert_not_called()
