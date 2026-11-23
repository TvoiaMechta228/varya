"""Общие моки: тесты не озвучивают ответы и не калибруют микрофон."""

from unittest.mock import Mock

import pytest

import speech
import settings
import conversation


@pytest.fixture(autouse=True)
def mute_speech(monkeypatch, tmp_path):
    """Подменить синтез и паузы для каждого теста."""
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(conversation, "memory_pressure", lambda: False)
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "settings.json")
    mock = Mock(return_value=True)
    monkeypatch.setattr(speech, "speak", mock)
    monkeypatch.setattr(speech.time, "sleep", Mock())
    monkeypatch.setattr(settings, "_current", {
        **settings.DEFAULTS, "voice": "sapi",
    })
    return mock


@pytest.fixture(scope="session")
def tk_root():
    """Разделить один интерпретатор Tk между тестами окон Windows."""
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()
