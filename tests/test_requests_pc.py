"""Проверки маршрута от свободной фразы до одного проверенного действия."""

import json
from time import monotonic
from unittest.mock import AsyncMock, Mock

import pytest

import actions
import conversation
from dialogue import Dialogue
import features
import main
import requests_pc as pc
import settings
import speech
from understanding import Intent
import window_buttons as buttons


@pytest.mark.parametrize("phrase", [
    "Напомни мне через четверть часа вынуть пирог",
    "Через пятнадцать минут напомни вынуть пирог",
    "Напомни вынуть пирог через 15 минут",
])
def test_offline_reminder(monkeypatch, phrase):
    monkeypatch.setattr(features, "_timers", [])
    monkeypatch.setattr(pc, "interpret", model := AsyncMock())
    state = Dialogue()
    main.route_command(phrase, dialogue=state, typed=True)
    assert features.remaining() == [("вынуть пирог", 900)]
    model.assert_not_awaited()


@pytest.mark.parametrize("phrase,expected", [
    ("открой настройки экрана", "ms-settings:display"),
    ("покажи папку загрузок", "Downloads"),
])
def test_local_destinations(monkeypatch, phrase, expected):
    monkeypatch.setattr(pc.os, "startfile", start := Mock())
    monkeypatch.setattr(pc.shell, "SHGetKnownFolderPath", lambda _: "Downloads")
    main.route_command(phrase, dialogue=Dialogue())
    start.assert_called_once_with(expected)


def test_unknown_path_from_model_cannot_be_opened(monkeypatch):
    monkeypatch.setattr(pc.os, "startfile", start := Mock())
    assert "Доступны" in pc.open_location("folder", "file:///anything.exe").text
    start.assert_not_called()


@pytest.mark.parametrize("value", [
    {"action": "shell", "argument": "x", "seconds": 0},
    {"action": "timer", "argument": "чай", "seconds": True},
    {"action": "timer", "argument": "чай", "seconds": -1},
    {"action": "app", "argument": "x", "seconds": 2},
    {"action": "app", "argument": "x", "seconds": 0, "call": "shutdown"},
])
def test_schema_rejects_unusable_proposal(value):
    with pytest.raises(ValueError):
        Intent.parse(json.dumps(value))


def test_model_search_executes_without_extra_confirmation(monkeypatch):
    monkeypatch.setattr(pc, "interpret", AsyncMock(return_value=Intent("search", "оладьи & кефир")))
    monkeypatch.setattr(conversation, "enabled", True)
    monkeypatch.setattr(settings, "_current", {**settings.DEFAULTS, "wake_word": True})
    monkeypatch.setattr(actions, "search_web", search := Mock())
    session = Dialogue()
    main.route_command("Варя, отыщи рецепт оладий на кефире", dialogue=session)
    search.assert_called_once_with("оладьи & кефир")
    assert session.proposal is None


@pytest.mark.parametrize("answer", ["нет", "таймер", "запомни", "который час"])
def test_intervening_reply_invalidates_offer(monkeypatch, answer):
    session = Dialogue(proposal=pc.Proposal(Intent("app", "браузер"), monotonic() + 60))
    monkeypatch.setattr(actions, "open_app", launch := Mock())
    main.route_command(answer, dialogue=session)
    assert session.proposal is None
    launch.assert_not_called()


def test_expired_offer(monkeypatch):
    session = Dialogue(proposal=pc.Proposal(Intent("app", "браузер"), monotonic() - 1))
    monkeypatch.setattr(actions, "open_app", launch := Mock())
    assert "истекло" in pc.confirmation(session, "да").text
    launch.assert_not_called()


def test_cancelled_inference_never_performs(monkeypatch):
    monkeypatch.setattr(pc, "interpret", AsyncMock(return_value=Intent("quieter")))
    monkeypatch.setattr(speech, "is_cancelled", lambda: True)
    monkeypatch.setattr(actions, "media_control", action := Mock())
    assert not pc.understand(Dialogue(), "Очень громкая музыка").text
    action.assert_not_called()


def test_model_offline_then_local_request(monkeypatch, mute_speech):
    monkeypatch.setattr(pc, "interpret", AsyncMock(side_effect=ConnectionRefusedError()))
    monkeypatch.setattr(conversation, "enabled", True)
    monkeypatch.setattr(actions, "media_control", action := Mock())
    session = Dialogue()
    main.route_command("слишком громко играет", dialogue=session)
    assert "локальной моделью" in mute_speech.call_args.args[0]
    main.route_command("сделай чуть потише", dialogue=session)
    action.assert_called_once_with("тише")


def test_numbered_selection_checks_live_button_and_window(monkeypatch):
    target = (12, 34, "Проверка")
    first = buttons.Button((1, 2), "OK")
    second = buttons.Button((1, 3), "OK")
    live = [target]
    action = Mock()
    monkeypatch.setattr(buttons.desktop, "windows", lambda: live)
    monkeypatch.setattr(buttons, "_elements", lambda hwnd: [(second, action)])
    session = Dialogue(buttons=(first, second), button_target=target)
    assert len(buttons.choose(session.buttons, "OK")) == 2
    assert "Да или нет" in pc.direct(session, "один").text
    assert "изменилась" in pc.confirmation(session, "да").text
    action.Invoke.assert_not_called()
    pc.direct(session, "два")
    live.clear()
    assert "изменилось" in pc.confirmation(session, "да").text
    action.Invoke.assert_not_called()
    live.append(target)
    pc.direct(session, "выбери номер 2")
    assert "Нажала" in pc.confirmation(session, "да").text
    action.Invoke.assert_called_once()


def test_quick_reply_expires_and_preserves_keyboard(monkeypatch, tk_root):
    import tkinter as tk
    import gui

    monkeypatch.setattr(gui, "AssistantController", Mock())
    monkeypatch.setattr(conversation, "enabled", False)
    root = tk.Toplevel(tk_root)
    try:
        window = gui.VaryaWindow(root)
        window.show_choices(7, monotonic() + 60, ("На полчаса",))
        window.quick.winfo_children()[0].invoke()
        window.controller.commands.put.assert_called_once_with(("choice", (7, "На полчаса")))
        assert not window.quick.winfo_children()
        window.show_choices(7, monotonic() - 1, ("Да", "Нет"))
        assert not window.quick.winfo_children()
    finally:
        root.destroy()


def test_ambiguous_window_choice_keeps_identity(monkeypatch):
    first, second = (1, 11, "Документ A"), (2, 22, "Документ Б")
    live = [first, second]
    monkeypatch.setattr(buttons.desktop, "windows", lambda: live)
    monkeypatch.setattr(buttons, "buttons", scan := Mock(return_value=()))
    state = Dialogue()
    main.route_command("покажи кнопки в окне Документ", dialogue=state)
    assert state.window_choices == (first, second)
    live[1] = (2, 33, second[2])
    main.route_command("два", dialogue=state)
    scan.assert_not_called()
    main.route_command("покажи кнопки в окне Документ", dialogue=state)
    main.route_command("один", dialogue=state)
    scan.assert_called_once_with(1)


def test_confirmation_without_proposal_is_not_inferred(monkeypatch):
    monkeypatch.setattr(pc, "interpret", model := AsyncMock())
    monkeypatch.setattr(conversation, "enabled", True)
    main.route_command("да", dialogue=Dialogue(last_result="Открыть браузер?"))
    model.assert_not_awaited()
