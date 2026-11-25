"""Регрессии диалога, ввода при работающем микрофоне и потока текста."""

from queue import Queue
from threading import Event
import tkinter as tk
from unittest.mock import MagicMock, Mock

import pytest

import conversation
import dialogue
import features
from legacy import gui
import main
import settings
import speech


@pytest.mark.parametrize("request_text,answer,expected", [
    ("Пожалуйста, поищи в Яндексе C++ & Qt?", "", "C++ & Qt?"),
    ("Найди   в интернете   C++ & Qt?", "", "C++ & Qt?"),
    ("поищи", "выключи компьютер", "выключи компьютер"),
])
def test_search_preserves_argument(monkeypatch, request_text, answer, expected):
    state = dialogue.Dialogue()
    search = Mock()
    monkeypatch.setattr(main.actions, "search_web", search)
    monkeypatch.setattr(main.actions, "shutdown_pc", shutdown := Mock())
    main.route_command(request_text, dialogue=state)
    if answer:
        search.assert_not_called()
        main.route_command(answer, dialogue=state)
    search.assert_called_once_with(expected)
    shutdown.assert_not_called()


def test_question_can_be_answered_by_keyboard(monkeypatch):
    monkeypatch.setattr(settings, "_current", {**settings.DEFAULTS, "wake_word": True})
    monkeypatch.setattr(features, "add_timer", timer := Mock(return_value="Таймер"))
    state = dialogue.Dialogue()
    main.route_command("Варя, поставь таймер", dialogue=state)
    main.route_command("на минуту", dialogue=state, typed=True)
    timer.assert_called_once_with("", 60)


def test_cancellation_and_deadline(monkeypatch):
    state = dialogue.Dialogue()
    monkeypatch.setattr(dialogue.time, "monotonic", now := Mock(return_value=10))
    assert state.prepare("запиши заметку")[1]
    assert state.prepare("отмена") == ("", "Отменила.")
    assert state.prepare("запиши заметку")[1]
    now.return_value += 61
    assert state.prepare("который час") == ("который час", "")


def test_single_calibration_per_session(monkeypatch):
    recognizer = Mock()
    monkeypatch.setattr(speech, "_recognizer", recognizer)
    monkeypatch.setattr(speech, "_calibrated", False)
    monkeypatch.setattr(speech.sr, "Microphone", MagicMock())
    speech.listen()
    speech.listen()
    assert recognizer.adjust_for_ambient_noise.call_count == 1
    speech.new_session()
    speech.listen()
    assert recognizer.adjust_for_ambient_noise.call_count == 2


@pytest.mark.parametrize("cancel", [False, True])
def test_keyboard_during_capture(monkeypatch, cancel):
    entered, release, routed = Event(), Event(), Event()

    def listen():
        entered.set()
        release.wait(4)
        return None

    def route(text, **kwargs):
        assert text == "вопрос" and kwargs["typed"]
        routed.set()
        return True

    monkeypatch.setattr(speech, "listen", listen)
    monkeypatch.setattr(main, "route_command", route)
    controller = gui.AssistantController(Queue())
    try:
        controller.start()
        assert entered.wait(4)
        controller.commands.put(("typed", "вопрос"))
        if cancel:
            controller.stop()
        release.set()
        if cancel:
            while controller.events.get(timeout=4)[0] != "stopped":
                pass
            assert not routed.is_set()
        else:
            assert routed.wait(4)
    finally:
        release.set()
        controller.close()
        controller.thread.join(4)


def test_chat_view_replaces_stream_once(monkeypatch, tk_root):
    monkeypatch.setattr(conversation, "enabled", False)
    monkeypatch.setattr(gui, "AssistantController", Mock())
    root = tk.Toplevel(tk_root)
    root.withdraw()
    try:
        window = gui.VaryaWindow(root)
        assert conversation.enabled
        for role, text in (("user", "привет"), ("delta", "При"), ("delta", "вет!"),
                           ("end_draft", ""), ("assistant", "Привет!")):
            window.append_turn(role, text)
        assert window.journal.get("1.0", "end-1c") == "Вы\nпривет\n\nВаря\nПривет!\n\n"
    finally:
        root.destroy()
