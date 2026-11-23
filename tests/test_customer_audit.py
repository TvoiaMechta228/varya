"""Регрессии пользовательских сценариев, выявленные при аудите."""

import json
from unittest.mock import Mock

import pytest

import conversation
import features
import gui
import main
import safety
import settings
import speech

_REAL_SPEAK = speech.speak


def test_timer_restores_after_restart_and_fires_once(monkeypatch):
    monkeypatch.setattr(features, "_timers", [])
    clock = [100.0, 1000.0]
    monkeypatch.setattr(features.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(features.time, "time", lambda: clock[1])
    features.add_timer("Чай", 60)
    monkeypatch.setattr(features, "_timers", [])
    clock[:] = [10.0, 1025.0]
    assert features.load_timers() is None
    assert features.remaining() == [("Чай", 35)]
    clock[:] = [46.0, 1061.0]
    assert features.expired_names() == ["Чай"]
    assert features.expired_names() == []
    features.load_timers()
    assert features.remaining() == []


def test_cancelled_timer_does_not_reappear(monkeypatch):
    monkeypatch.setattr(features, "_timers", [])
    features.add_timer("Кофе", 100)
    features.cancel_timer("Кофе")
    features.load_timers()
    assert features.remaining() == []


@pytest.mark.parametrize("value", [
    {}, [{"name": "x", "deadline": "tomorrow"}],
    [{"name": "x", "deadline": float("nan")}], [None],
])
def test_corrupt_timer_store_preserves_running_timers(monkeypatch, value):
    monkeypatch.setattr(features, "_timers", [("Работа", 123)])
    (settings.DATA_DIR / "timers.json").write_text(json.dumps(value), encoding="utf-8")
    assert features.load_timers()
    assert features._timers == [("Работа", 123)]


def test_typed_answer_does_not_initialize_sound(monkeypatch):
    monkeypatch.setattr(speech, "_engine", None)
    token = speech.quiet_reply.set(True)
    try:
        assert _REAL_SPEAK("Готово")
        assert speech._engine is None
    finally:
        speech.quiet_reply.reset(token)


def test_quiet_mode_preserves_audible_danger_confirmation(monkeypatch):
    heard = []
    monkeypatch.setattr(speech, "speak", lambda _: heard.append(speech.quiet_reply.get()) or True)
    monkeypatch.setattr(speech, "listen", lambda **_: "да выключение")
    token = speech.quiet_reply.set(True)
    try:
        assert safety.confirm("shutdown")
        assert heard == [False]
        assert speech.quiet_reply.get()
    finally:
        speech.quiet_reply.reset(token)


def test_configurable_model_preserves_existing_settings():
    settings.save({**settings.DEFAULTS, "model": "qwen2.5:1.5b", "microphone": 3,
                   "speak_typed": True, "save_history": False})
    settings.load()
    assert conversation.model_name() == "qwen2.5:1.5b"
    assert settings.snapshot()["microphone"] == 3
    assert settings.snapshot()["speak_typed"]
    assert not settings.snapshot()["save_history"]


def test_no_history_skips_audit(monkeypatch):
    import missions
    monkeypatch.setitem(settings._current, "save_history", False)
    monkeypatch.setattr(missions.files, "record", record := Mock())
    missions.audit("Запрос", "Личное сообщение")
    record.assert_not_called()


def test_oversized_request_never_enters_actions(monkeypatch):
    monkeypatch.setattr(main.actions, "open_app", launch := Mock())
    main.route_command("открой " + "a" * 2100, typed=True)
    launch.assert_not_called()


def test_settings_apply_mic_and_silent_text(monkeypatch, tk_root):
    import tkinter as tk
    monkeypatch.setattr(gui, "AssistantController", Mock())
    monkeypatch.setattr(conversation, "enabled", False)
    root = tk.Toplevel(tk_root)
    try:
        window = gui.VaryaWindow(root)
        window.device_ids["Тестовый микрофон"] = 2
        window.microphone_var.set("Тестовый микрофон")
        window.speak_typed.set(False)
        window.model_var.set("qwen2.5:1.5b")
        assert window.save_settings()
        assert settings.snapshot()["microphone"] == 2
        assert conversation.model_name() == "qwen2.5:1.5b"
        window.preview_voice()
        assert window.busy and window.sending
        assert str(window.preview_button["state"]) == "disabled"
    finally:
        root.destroy()


def test_stop_interrupts_microphone_stream(monkeypatch):
    monkeypatch.setattr(speech, "is_cancelled", lambda: True)
    source = Mock()
    with pytest.raises(InterruptedError):
        speech.MeterStream(source).read(1024)
    source.read.assert_not_called()


def test_selected_microphone_is_used(monkeypatch):
    monkeypatch.setitem(settings._current, "microphone", 4)
    device = Mock(side_effect=OSError("missing"))
    monkeypatch.setattr(speech.sr, "Microphone", device)
    speech.listen()
    device.assert_called_once_with(device_index=4)


def test_microphone_cancellation_does_not_report_failure(monkeypatch):
    monkeypatch.setattr(speech.sr, "Microphone", Mock(side_effect=InterruptedError))
    monkeypatch.setattr(speech, "publish", events := Mock())
    assert speech.listen() is None
    assert not any(call.args[0] == "mic_unavailable" for call in events.call_args_list)


def test_repeated_network_failures_do_not_repeat_voice_warning(monkeypatch, mute_speech):
    from unittest.mock import MagicMock
    monkeypatch.setattr(speech.sr, "Microphone", MagicMock())
    recognizer = Mock()
    recognizer.recognize_google.side_effect = speech.sr.RequestError("offline")
    monkeypatch.setattr(speech, "_recognizer", recognizer)
    monkeypatch.setattr(speech, "_last_service_error", float("-inf"))
    speech.listen()
    speech.listen()
    assert mute_speech.call_count == 1
