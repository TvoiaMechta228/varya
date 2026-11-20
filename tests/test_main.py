"""Проверки маршрутов и устойчивости основного цикла."""

from datetime import datetime
from unittest.mock import Mock

import pytest

import main


@pytest.mark.parametrize("text,expected", [
    ("найди в интернете котики", "котики"),
    ("  Найди  в интернете  C++ & Python?  ", "C++ & Python?"),
    ("найди в интернете", ""),
    ("найди в интернете   ", ""),
    ("найди в интернетекотики", None),
    ("пожалуйста найди в интернете котики", None),
])
def test_parse_search(text, expected):
    """Выделять запрос без потери символов и ложных префиксов."""
    assert main.parse_search_query(text) == expected


@pytest.mark.parametrize("text,action,args", [
    ("открой блокнот", "open_app", ("блокнот",)),
    ("найди в интернете C++ & Python", "search_web", ("C++ & Python",)),
    ("скриншот", "take_screenshot", ()),
    ("выключи компьютер", "shutdown_pc", ()),
    ("перезагрузи компьютер", "reboot_pc", ()),
    ("отмени выключение", "cancel_shutdown", ()),
])
def test_routes(monkeypatch, text, action, args):
    """Направлять каждую команду только в нужное действие."""
    mocks = {}
    for name in (
        "open_app", "search_web", "take_screenshot", "shutdown_pc",
        "reboot_pc", "cancel_shutdown",
    ):
        mocks[name] = Mock(return_value=True)
        monkeypatch.setattr(main.actions, name, mocks[name])
    assert main.route_command(text)
    mocks[action].assert_called_once_with(*args)
    for name, mock in mocks.items():
        if name != action:
            mock.assert_not_called()


@pytest.mark.parametrize("text", ["стоп", "ВЫХОД!", " пока "])
def test_exit(text):
    """Команды выхода завершают цикл."""
    assert main.route_command(text) is False


@pytest.mark.parametrize("text", ["который час", "сколько времени"])
def test_time(monkeypatch, mute_speech, text):
    """Озвучивать локальное время."""
    monkeypatch.setattr(main, "datetime", Mock(
        now=Mock(return_value=datetime(2026, 1, 1, 12, 34)),
    ))
    assert main.route_command(text)
    mute_speech.assert_called_once_with("Сейчас 12:34.")


@pytest.mark.parametrize("text", ["что-нибудь", "да, выключение", ""])
def test_unknown(text, mute_speech):
    """Подтверждение вне диалога также считается неизвестной командой."""
    assert main.route_command(text)
    mute_speech.assert_called_once_with("Команда не распознана.")


def test_empty_search(monkeypatch):
    """Пустой запрос не открывает браузер."""
    search = Mock()
    monkeypatch.setattr(main.actions, "search_web", search)
    assert main.route_command("найди в интернете")
    search.assert_not_called()


def test_action_error(monkeypatch):
    """Ошибка ОС не прекращает маршрутизацию."""
    monkeypatch.setattr(main.actions, "open_app", Mock(side_effect=OSError))
    assert main.route_command("открой блокнот")


def test_unlisted_app_response(monkeypatch, mute_speech):
    """Отклонённая программа получает понятный ответ."""
    monkeypatch.setattr(main.actions, "open_app", Mock(return_value=False))
    assert main.route_command("открой powershell")
    mute_speech.assert_called_once_with("Этой программы нет в белом списке.")


@pytest.mark.parametrize("command", [
    "выключи компьютер", "перезагрузи компьютер",
])
@pytest.mark.parametrize("answer", [None, "нет", "да"])
def test_power_route_without_confirmation(monkeypatch, command, answer):
    """Проверить маршрут до ОС с настоящей логикой подтверждения."""
    run = Mock()
    monkeypatch.setattr(main.actions, "_require_windows", Mock())
    monkeypatch.setattr(main.actions.subprocess, "run", run)
    monkeypatch.setattr(main.speech, "listen", Mock(return_value=answer))
    assert main.route_command(command)
    run.assert_not_called()


def test_loop_survives_timeout(monkeypatch):
    """После отсутствия текста цикл обрабатывает следующую команду."""
    monkeypatch.setattr(main, "setup_logging", Mock())
    listen = Mock(side_effect=[None, "неизвестно", "стоп"])
    monkeypatch.setattr(main.speech, "listen", listen)
    main.main()
    assert listen.call_count == 3
