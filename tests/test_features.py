"""Проверки новых команд, хранения данных и нейросинтеза с моками."""

import json
from types import SimpleNamespace
from unittest.mock import Mock
import sys

import pytest

import actions
import features
import main
import neural_voice
import settings
import speech
from test_speech import _REAL_SPEAK


@pytest.fixture(autouse=True)
def isolated_data(monkeypatch, tmp_path):
    """Не использовать реальные заметки, настройки и таймеры пользователя."""
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(features, "_timers", [])
    monkeypatch.setattr(speech, "_neural_retry_at", 0)


@pytest.mark.parametrize("text,result", [
    ("пять минут", 300), ("одну минуту", 60), ("два часа", 7200),
    ("двадцать три секунды", 23), ("24 часа", 86400),
    ("0 секунд", None), ("25 часов", None), ("foo минут", None),
    ("пять пять минут", None), ("-5 минут", None),
])
def test_duration(text, result):
    assert features.duration(text) == result


@pytest.mark.parametrize("text,result", [
    ("12 плюс 3", "15"), ("1,5 умножить на 2", "3"),
    ("2 разделить на 0", "На ноль делить нельзя."),
    ("-2 минус 3", "-5"), ("5 / 2", "2,5"), ("1-1", "0"),
])
def test_calculate(text, result):
    assert features.calculate(text) == result


def test_no_code_evaluation():
    assert "например" in features.calculate("__import__('os').system('calc')")


def test_timer_lifecycle(monkeypatch):
    clock = Mock(return_value=100)
    monkeypatch.setattr(features.time, "monotonic", clock)
    assert main.route_command("таймер на пять секунд")
    assert features.due_timers() == 0
    clock.return_value = 105
    assert features.due_timers() == 1
    assert features.due_timers() == 0


def test_cancel_timers():
    main.route_command("таймер на 5 минут")
    main.route_command("отмени таймеры")
    assert not features._timers


def test_notes_preserve_punctuation():
    main.route_command("Варя, запомни Купить C++ & Python!")
    data = json.loads((settings.DATA_DIR / "notes.json").read_text('utf-8'))
    assert data[0]["text"] == "Купить C++ & Python!"


def test_broken_notes_are_not_overwritten():
    path = settings.DATA_DIR / "notes.json"
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        features.remember("новая запись")
    assert path.read_text('utf-8') == "broken"


def test_wake_word_blocks_without_prefix(monkeypatch):
    monkeypatch.setattr(settings, "_current", {
        **settings.DEFAULTS, "wake_word": True,
    })
    open_app = Mock(return_value=True)
    monkeypatch.setattr(actions, "open_app", open_app)
    main.route_command("открой блокнот")
    open_app.assert_not_called()
    main.route_command("Варя, открой блокнот")
    open_app.assert_called_once_with("блокнот")


def test_settings_validation_and_reload():
    settings.save({"voice": "bad", "rate": 999, "volume": -1,
                   "wake_word": "false", "shell": "evil"})
    settings.load()
    assert settings.snapshot() == {
        **settings.DEFAULTS, "voice": "neural", "rate": 230, "volume": 10, "wake_word": False,
    }


@pytest.mark.parametrize("command,key", list(actions.MEDIA_KEYS.items()))
def test_media_keys_are_fixed(monkeypatch, command, key):
    press = Mock()
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(press=press))
    if command == "пауза музыки":
        actions.media_control(command)
    else:
        assert main.route_command(command)
    press.assert_called_once_with(
        key, presses=3 if command in {"громче", "тише"} else 1,
    )


def test_neural_fallback(monkeypatch):
    monkeypatch.setattr(settings, "_current", dict(settings.DEFAULTS))
    monkeypatch.setattr(neural_voice, "speak", Mock(side_effect=OSError))
    engine = Mock()
    monkeypatch.setattr(speech, "_engine", engine)
    assert _REAL_SPEAK("Проверка")
    engine.say.assert_called_once_with("Проверка")
    assert speech._neural_retry_at > 0


def test_neural_success(monkeypatch):
    monkeypatch.setattr(settings, "_current", dict(settings.DEFAULTS))
    neural = Mock(return_value=True)
    monkeypatch.setattr(neural_voice, "speak", neural)
    monkeypatch.setattr(speech, "_engine", None)
    assert _REAL_SPEAK("Проверка")
    assert speech._engine is None
    assert neural.call_args.args[1:3] == (175, 85)


def test_stop_works_without_wake_word(monkeypatch):
    monkeypatch.setattr(settings, "_current", {
        **settings.DEFAULTS, "wake_word": True,
    })
    assert main.route_command("стоп") is False


def test_closing_cancels_power_confirmation(monkeypatch):
    import threading
    import safety

    closing = threading.Event()
    monkeypatch.setattr(speech, "shutdown_event", closing)

    def answer(**kwargs):
        closing.set()
        return "да выключение"

    monkeypatch.setattr(speech, "listen", answer)
    assert not safety.confirm("shutdown")
