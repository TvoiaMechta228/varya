"""Общие моки: тесты не озвучивают ответы и не калибруют микрофон."""

from unittest.mock import Mock

import pytest

import speech


@pytest.fixture(autouse=True)
def mute_speech(monkeypatch):
    """Подменить синтез и паузы для каждого теста."""
    mock = Mock(return_value=True)
    monkeypatch.setattr(speech, "speak", mock)
    monkeypatch.setattr(speech.time, "sleep", Mock())
    return mock
