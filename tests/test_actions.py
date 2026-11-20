"""Проверки белого списка и безопасных вызовов Windows."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse
import sys

import pytest

import actions
import config


@pytest.mark.parametrize("name", list(config.APP_WHITELIST))
def test_whitelisted_app(monkeypatch, name):
    """Использовать только аргументы из конфигурации."""
    popen = Mock()
    browser = Mock(return_value=True)
    monkeypatch.setattr(actions, "_require_windows", Mock())
    monkeypatch.setattr(actions.subprocess, "Popen", popen)
    monkeypatch.setattr(actions.webbrowser, "open", browser)
    assert actions.open_app(name)
    command = config.APP_WHITELIST[name]
    if command is None:
        browser.assert_called_once_with(config.BROWSER_URL, new=2)
        popen.assert_not_called()
    else:
        popen.assert_called_once_with(command, shell=False)
        browser.assert_not_called()


@pytest.mark.parametrize("name", [
    "cmd", "powershell", "notepad.exe", "блокнот & calc", "", "../calc.exe",
])
def test_reject_unknown_app(monkeypatch, name):
    """Неизвестное имя или попытка инъекции не запускают процесс."""
    popen = Mock()
    browser = Mock()
    monkeypatch.setattr(actions.subprocess, "Popen", popen)
    monkeypatch.setattr(actions.webbrowser, "open", browser)
    assert not actions.open_app(name)
    popen.assert_not_called()
    browser.assert_not_called()


def test_search_encoding(monkeypatch):
    """Спецсимволы остаются частью одного поискового параметра."""
    browser = Mock(return_value=True)
    monkeypatch.setattr(actions.webbrowser, "open", browser)
    query = 'C++ & котики #1 / "тест"'
    actions.search_web(query)
    url = urlparse(browser.call_args.args[0])
    assert (url.scheme, url.netloc, url.path) == (
        "https", "yandex.ru", "/search/",
    )
    assert parse_qs(url.query) == {"text": [query]}
    assert not url.fragment


@pytest.mark.parametrize("name,key,flag", [
    ("shutdown_pc", "shutdown", "/s"),
    ("reboot_pc", "reboot", "/r"),
])
@pytest.mark.parametrize("approved", [False, True])
def test_power_requires_confirmation(monkeypatch, name, key, flag, approved):
    """Прямой вызов действия также обязан получить подтверждение."""
    confirm = Mock(return_value=approved)
    run = Mock()
    monkeypatch.setattr(actions, "_require_windows", Mock())
    monkeypatch.setattr(actions.safety, "confirm", confirm)
    monkeypatch.setattr(actions.subprocess, "run", run)
    assert getattr(actions, name)() is approved
    confirm.assert_called_once_with(key)
    if approved:
        run.assert_called_once_with(
            [config.SHUTDOWN_EXE, flag, "/t", "10"],
            check=True, shell=False,
        )
    else:
        run.assert_not_called()


def test_cancel_shutdown(monkeypatch):
    """Отмена не нуждается в подтверждении и вызывает только /a."""
    monkeypatch.setattr(actions, "_require_windows", Mock())
    run = Mock()
    monkeypatch.setattr(actions.subprocess, "run", run)
    actions.cancel_shutdown()
    run.assert_called_once_with(
        [config.SHUTDOWN_EXE, "/a"], check=True, shell=False,
    )


def test_screenshot_collision(monkeypatch, tmp_path):
    """Скриншот не затирает файл с той же секундой."""
    monkeypatch.setattr(config, "PICTURES_DIR", tmp_path)
    monkeypatch.setattr(actions, "datetime", Mock(
        now=Mock(return_value=datetime(2026, 1, 2, 3, 4, 5)),
    ))
    image = Mock()
    image.save.side_effect = lambda stream, **kwargs: stream.write(b"mock png")
    monkeypatch.setitem(sys.modules, "pyautogui", SimpleNamespace(
        screenshot=Mock(return_value=image),
    ))
    first = actions.take_screenshot()
    second = actions.take_screenshot()
    assert first.name == "screen_20260102_030405.png"
    assert second.name == "screen_20260102_030405_1.png"
    assert first.read_bytes() == second.read_bytes() == b"mock png"
