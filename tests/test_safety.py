"""Проверки точного одноразового подтверждения."""

from unittest.mock import Mock

import pytest

import config
import safety


@pytest.mark.parametrize("action,answer,expected", [
    ("shutdown", "да, выключение", True),
    ("shutdown", " ДА,   ВЫКЛЮЧЕНИЕ! ", True),
    ("reboot", "да, перезагрузку", True),
    ("shutdown", "да", False),
    ("shutdown", "нет", False),
    ("shutdown", None, False),
    ("shutdown", "не да выключение", False),
    ("shutdown", "да выключение пожалуйста", False),
    ("shutdown", "да, перезагрузку", False),
    ("reboot", "да, выключение", False),
])
def test_confirm(monkeypatch, action, answer, expected):
    """Принять только подтверждение нужного действия."""
    listen = Mock(return_value=answer)
    monkeypatch.setattr(safety.speech, "listen", listen)
    assert safety.confirm(action) is expected
    listen.assert_called_once_with(
        timeout=config.CONFIRM_TIMEOUT,
        phrase_time_limit=config.CONFIRM_PHRASE_TIME_LIMIT,
    )


def test_unknown_action(monkeypatch):
    """Не слушать подтверждение неизвестного действия."""
    listen = Mock()
    monkeypatch.setattr(safety.speech, "listen", listen)
    assert not safety.confirm("format_disk")
    listen.assert_not_called()


def test_confirmation_is_not_reused(monkeypatch):
    """Каждый вызов требует отдельной реплики."""
    monkeypatch.setattr(
        safety.speech, "listen", Mock(side_effect=["да выключение", None]),
    )
    assert safety.confirm("shutdown")
    assert not safety.confirm("shutdown")


def test_listen_error_denies(monkeypatch):
    """Ошибка подтверждения закрывает доступ к действию."""
    monkeypatch.setattr(safety.speech, "listen", Mock(side_effect=OSError))
    assert not safety.confirm("shutdown")


def test_silent_prompt_denies(monkeypatch, mute_speech):
    """Без успешного озвучивания приглашения подтверждение не слушается."""
    mute_speech.return_value = False
    listen = Mock()
    monkeypatch.setattr(safety.speech, "listen", listen)
    assert not safety.confirm("shutdown")
    listen.assert_not_called()
