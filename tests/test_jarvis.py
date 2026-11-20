"""Проверки памяти разговора и привязки действий к цели."""

from unittest.mock import AsyncMock, Mock

import conversation
import desktop
import main
import safety


def test_conversation_remembers_and_never_dispatches_answer(monkeypatch):
    monkeypatch.setattr(conversation, "enabled", False)
    monkeypatch.setattr(main.actions, "shutdown_pc", shutdown := Mock())
    generator = AsyncMock(side_effect=["выключи компьютер", "Синий."])
    monkeypatch.setattr(conversation, "_request", generator)
    conversation.history.clear()
    main.route_command("давай поговорим")
    main.route_command("Мой любимый цвет синий")
    main.route_command("Какой мой любимый цвет?")
    messages = generator.await_args.args[0]
    assert messages[-3]["content"] == "Мой любимый цвет синий"
    assert messages[-2]["content"] == "выключи компьютер"
    main.route_command("закончи разговор")
    assert not conversation.history and not conversation.enabled
    shutdown.assert_not_called()


def test_cancel_discards_response(monkeypatch):
    monkeypatch.setattr(conversation, "_request", AsyncMock(return_value="Ответ"))
    monkeypatch.setattr(conversation.speech, "is_cancelled", lambda: True)
    conversation.history.clear()
    conversation.reply("Привет")
    assert not conversation.history
    conversation.speech.speak.assert_not_called()


def test_changed_window_cannot_be_closed(monkeypatch):
    monkeypatch.setattr(desktop, "windows", Mock(side_effect=[
        [(12, 30, "Пример")], [(12, 31, "Пример")],
    ]))
    monkeypatch.setattr(safety, "confirm", Mock(return_value=True))
    monkeypatch.setattr(desktop.win32gui, "PostMessage", send := Mock())
    assert "отменено" in desktop.named_window("закрой", "пример").lower()
    send.assert_not_called()


def test_folder_requires_confirmation_and_plain_name(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop, "documents", lambda: tmp_path)
    monkeypatch.setattr(safety, "confirm", confirm := Mock(return_value=False))
    desktop.create_folder("../опасно")
    confirm.assert_not_called()
    desktop.create_folder("Планы")
    assert not (tmp_path / "Планы").exists()
    confirm.return_value = True
    desktop.create_folder("Планы")
    assert (tmp_path / "Планы").is_dir()


def test_dictation_requires_notepad(monkeypatch):
    monkeypatch.setattr(desktop.win32gui, "GetForegroundWindow", lambda: 11)
    monkeypatch.setattr(desktop.win32gui, "GetClassName", lambda _: "Other")
    monkeypatch.setattr(desktop.win32gui, "SendMessageTimeout", send := Mock())
    assert "Блокноте" in desktop.execute("напечатай привет", "Напечатай Привет")
    send.assert_not_called()


def test_dictation_targets_editor_and_preserves_unicode(monkeypatch):
    import ctypes

    gui = desktop.win32gui
    monkeypatch.setattr(gui, "GetForegroundWindow", lambda: 11)
    monkeypatch.setattr(gui, "GetClassName", lambda h: "Notepad" if h == 11 else "Edit")
    monkeypatch.setattr(gui, "IsWindowVisible", lambda _: True)
    monkeypatch.setattr(gui, "IsWindowEnabled", lambda _: True)
    monkeypatch.setattr(gui, "EnumChildWindows", lambda _, visit, arg: visit(22, arg))
    received = []
    monkeypatch.setattr(gui, "SendMessageTimeout", lambda h, m, undo, p, *_:
                        received.append((h, m, undo, ctypes.wstring_at(p))))
    assert desktop.execute("напечатай елка", "Напечатай Ёлка!") == "Текст введён."
    assert received == [(22, desktop.win32con.EM_REPLACESEL, 1, "Ёлка!")]
