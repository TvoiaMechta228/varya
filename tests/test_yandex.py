"""Яндекс открывается в обычном браузере, ограничения сайтов не скрываются."""

from unittest.mock import Mock

import pytest

import actions
import browser_tools


def test_native_search_uses_installed_yandex_session(monkeypatch, tmp_path):
    monkeypatch.setenv("VARYA_NATIVE", "1")
    executable = tmp_path / "browser.exe"
    monkeypatch.setattr(actions, "yandex_executable", lambda: executable)
    popen = Mock()
    managed = Mock()
    monkeypatch.setattr(actions.subprocess, "Popen", popen)
    monkeypatch.setattr(browser_tools, "open_url", managed)
    actions.search_web("котики & C++")
    args, kwargs = popen.call_args
    assert args[0][0] == str(executable)
    assert args[0][1].startswith("https://yandex.ru/search/?text=")
    assert kwargs == {
        "shell": False,
        "creationflags": actions.subprocess.CREATE_BREAKAWAY_FROM_JOB,
    }
    assert len(args[0]) == 2
    managed.assert_not_called()


def test_yandex_falls_back_to_default_browser(monkeypatch):
    monkeypatch.setenv("VARYA_NATIVE", "1")
    monkeypatch.setattr(actions, "yandex_executable", lambda: None)
    browser = Mock(return_value=True)
    monkeypatch.setattr(actions.webbrowser, "open", browser)
    actions.open_service("яндекс музыку")
    browser.assert_called_once_with("https://music.yandex.ru/", new=2)


def test_lookalike_domain_cannot_launch_yandex(monkeypatch):
    monkeypatch.setenv("VARYA_NATIVE", "1")
    launcher = Mock()
    managed = Mock()
    monkeypatch.setattr(actions.subprocess, "Popen", launcher)
    monkeypatch.setattr(browser_tools, "open_url", managed)
    actions._open_url("https://yandex.ru.other.invalid")
    launcher.assert_not_called()
    managed.assert_called_once_with("https://yandex.ru.other.invalid")


@pytest.mark.parametrize(
    "url,title,body,expected",
    [
        ("https://yandex.ru/showcaptcha?text=x", "Вы не робот?", "", "проверку"),
        (
            "https://music.yandex.ru/",
            "Музыка",
            "Яндекс Музыка недоступна в вашем регионе",
            "регион",
        ),
        ("https://ya.ru/", "Яндекс", "Поиск", None),
        ("https://unrelated.invalid/", "Вы не робот?", "", None),
    ],
)
def test_site_problem_is_reported_honestly(url, title, body, expected):
    result = browser_tools.page_problem(url, title, body)
    assert (result is None) if expected is None else expected in result
