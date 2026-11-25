"""Сквозные проверки команд, контекста, карточек и доступности ввода."""

from unittest.mock import AsyncMock, Mock
import tkinter as tk

import aiohttp
import pytest

import actions
import conversation
import desktop
from dialogue import Dialogue
import features
from legacy import gui
import main
import settings
import speech


@pytest.fixture
def session(monkeypatch, tmp_path):
    monkeypatch.setattr(features, "_timers", [])
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    return Dialogue()


def test_tea_conversation(session, monkeypatch, mute_speech):
    clock = Mock(return_value=100)
    monkeypatch.setattr(features.time, "monotonic", clock)
    for text in ("Варя, можешь поставить таймер для чая?", "ненадолго", "На полчаса"):
        main.route_command(text, dialogue=session)
    assert features.remaining() == [("Чай", 1800)]
    clock.return_value += 65
    main.route_command("Сколько по нему осталось?", dialogue=session)
    assert "28 минут 55 секунд" in mute_speech.call_args.args[0]
    main.route_command("Отмени его", dialogue=session)
    assert not features.remaining()


def test_choose_between_timers(session, mute_speech):
    features.add_timer("чай", 60)
    features.add_timer("кофе", 80)
    main.route_command("Отмени его", dialogue=session)
    assert "Какой таймер" in mute_speech.call_args.args[0]
    main.route_command("кофе", dialogue=session)
    assert [name for name, _ in features.remaining()] == ["чай"]


@pytest.mark.parametrize("phrase,opens", [
    ("Открой блокнот", True), ("Можешь открыть блокнот?", True),
    ("Запусти, пожалуйста, блокнот", True), ("Запусти приложение блокнот", True),
    ("Как открыть блокнот?", False), ("Не открывай блокнот", False),
])
def test_open_intent(session, monkeypatch, phrase, opens):
    monkeypatch.setattr(actions, "open_app", launch := Mock(return_value=True))
    main.route_command(phrase, dialogue=session)
    if opens:
        launch.assert_called_once_with("блокнот")
    else:
        launch.assert_not_called()


def test_catalog_requires_setting(session, monkeypatch):
    monkeypatch.setattr(actions, "open_installed", launch := Mock(return_value=True))
    main.route_command("запусти приложение Example", dialogue=session)
    launch.assert_not_called()
    monkeypatch.setattr(settings, "_current", {**settings.DEFAULTS, "installed_apps": True})
    main.route_command("открой Example", dialogue=session)
    launch.assert_called_once_with("example")


def test_note_reads_actual_storage(session, mute_speech):
    main.route_command("запиши заметку Купить C++!", dialogue=session)
    main.route_command("Прочитай эту заметку", dialogue=session)
    mute_speech.assert_called_with("Купить C++!")
    (settings.DATA_DIR / "notes.json").unlink()
    main.route_command("Прочитай эту заметку", dialogue=session)
    mute_speech.assert_called_with("Заметок пока нет.")


def test_window_selection_is_bound_to_original(session, monkeypatch):
    first, second = (3, 8, "А — Блокнот"), (4, 9, "Б — Блокнот")
    live = [first, second]
    monkeypatch.setattr(desktop, "windows", lambda: live)
    monkeypatch.setattr(desktop, "apply_window", apply := Mock(return_value="Готово."))
    main.route_command("разверни окно Блокнот", dialogue=session)
    apply.assert_not_called()
    main.route_command("А — Блокнот", dialogue=session)
    apply.assert_called_once_with("разверни", first)
    live[0] = (3, 99, first[2])
    main.route_command("закрой то окно", dialogue=session)
    assert apply.call_count == 1


def test_offline_chat_leaves_actions_available(session, monkeypatch, mute_speech):
    monkeypatch.setattr(conversation, "enabled", True)
    monkeypatch.setattr(conversation, "_request", AsyncMock(side_effect=aiohttp.ClientConnectionError()))
    main.route_command("О чём поговорим?", dialogue=session)
    assert "Ollama" in mute_speech.call_args.args[0]
    main.route_command("таймер чай на минуту", dialogue=session)
    assert features.remaining()[0][0] == "чай"


def test_layout_and_cards_at_125_percent(session, monkeypatch, tk_root):
    """Окно на рабочей области ноутбука при 120 DPI и после уменьшения."""
    scale = tk_root.tk.call("tk", "scaling")
    tk_root.tk.call("tk", "scaling", 120 / 72)
    root = tk.Toplevel(tk_root)
    monkeypatch.setattr(gui, "AssistantController", Mock())
    monkeypatch.setattr(conversation, "enabled", False)
    try:
        window = gui.VaryaWindow(root)
        window.append_turn("user", "Длинная строка " * 400)
        features.add_timer("Чай", 1800)
        window.card("timer_card", "Чай")
        for size in ("1366x768", "1093x614", "660x490", "860x660"):
            root.geometry(size)
            root.deiconify()
            root.update()
            assert window.entry.winfo_viewable()
            top = window.entry.winfo_rooty() - root.winfo_rooty()
            assert 0 < top < root.winfo_height() - window.entry.winfo_height()
            assert window.entry.winfo_width() > 130
        assert window.entry.bind("<Return>")
        assert root.bind("<Escape>")
        window.organizer.refresh()
        assert window.organizer.timers.exists("Чай")
        window.cancel_card("Чай")
        assert not window.timer_cards and not window.organizer.timers.exists("Чай")
    finally:
        root.destroy()
        tk_root.tk.call("tk", "scaling", scale)



def test_mixed_language_stream_keeps_finished_russian_sentence(monkeypatch):
    import asyncio
    import json
    from unittest.mock import MagicMock

    lines = [json.dumps({"message": {"content": token}}).encode()
             for token in ("Чай остывает.", " Из-за ", "气")]
    response = MagicMock()
    response.content.__aiter__.return_value = lines
    client = MagicMock()
    client.post.return_value.__aenter__.return_value = response
    manager = MagicMock()
    manager.__aenter__.return_value = client
    monkeypatch.setattr(conversation.aiohttp, "ClientSession", lambda **_: manager)
    monkeypatch.setattr(speech, "publish", publish := Mock())
    answer = asyncio.run(conversation._request([{"role": "user", "content": "Почему чай остывает?"}]))
    assert answer == "Чай остывает."
    assert all("气" not in call.args[-1] for call in publish.call_args_list)
