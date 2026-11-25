"""Проверки подтверждений, сохранности документов и новых инструментов."""

import json
from pathlib import Path
from unittest.mock import Mock, MagicMock

import pytest

import capabilities as tools
from dialogue import Dialogue
import document_tools as docs
import features
import main
import memory_store
import settings


def invoke(session, action, data):
    return tools.request(session, action, json.dumps(data, ensure_ascii=False))


def test_word_requires_confirmation_and_preserves_old_file(tmp_path):
    from docx import Document

    path = tmp_path / "report.docx"
    original = Document()
    original.add_paragraph("Старый текст")
    original.save(path)
    session = Dialogue()
    invoke(session, "word_append", {"path": str(path), "text": "Новый текст"})
    assert "Новый текст" not in docs.read(str(path))
    result = tools.confirmation(session, "да")
    assert "Новый текст" in docs.read(str(path))
    assert "Старый текст" in docs.read(str(path))
    assert "Резервная копия" in result.text


def test_stale_confirmation_never_overwrites_file(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("old", encoding="utf-8")
    session = Dialogue()
    invoke(session, "write_text", {"path": str(path), "text": "new"})
    path.write_text("another edit", encoding="utf-8")
    assert "изменился" in tools.confirmation(session, "да").text
    assert path.read_text(encoding="utf-8") == "another edit"


def test_denial_and_unrelated_request_discard_pending(tmp_path):
    path = tmp_path / "note.txt"
    session = Dialogue()
    for reply in ("нет", "другая просьба"):
        invoke(session, "write_text", {"path": str(path), "text": "new"})
        tools.confirmation(session, reply)
        assert session.capability is None
        assert not path.exists()


def test_excel_create_and_edit_verifies_value(tmp_path):
    path = tmp_path / "table.xlsx"
    session = Dialogue()
    invoke(session, "excel_set", {"path": str(path), "cell": "B2", "value": "Бюджет"})
    assert not path.exists()
    tools.confirmation(session, "да")
    assert "B2=Бюджет" in docs.read(str(path))
    invoke(session, "excel_set", {"path": str(path), "cell": "A1", "value": 42})
    tools.confirmation(session, "да")
    assert "B2=Бюджет" in docs.read(str(path))
    assert "A1=42" in docs.read(str(path))


def test_presentation_adds_slide(tmp_path):
    path = tmp_path / "slides.pptx"
    session = Dialogue()
    for title in ("Первый", "Второй"):
        invoke(
            session,
            "slide_add",
            {"path": str(path), "title": title, "text": "Текст слайда"},
        )
        tools.confirmation(session, "да")
    assert "Первый" in docs.read(str(path)) and "Второй" in docs.read(str(path))


def test_image_resize_keeps_source(tmp_path):
    from PIL import Image

    source, target = tmp_path / "original.png", tmp_path / "small.png"
    Image.new("RGB", (100, 80), "green").save(source)
    session = Dialogue()
    invoke(
        session,
        "image_edit",
        {"source": str(source), "path": str(target), "width": 50, "height": 40},
    )
    tools.confirmation(session, "да")
    with Image.open(target) as resized:
        assert resized.size == (50, 40)
    with Image.open(source) as original:
        assert original.size == (100, 80)


def test_file_copy_to_directory_and_repeated_confirmation(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("content")
    destination = tmp_path / "destination"
    destination.mkdir()
    session = Dialogue()
    invoke(session, "file_copy", {"source": str(source), "path": str(destination)})
    tools.confirmation(session, "да")
    assert (destination / source.name).read_text() == "content"
    assert source.exists()
    assert tools.confirmation(session, "да") is None


def test_local_word_command_routes_and_confirms(tmp_path):
    path = tmp_path / "new.docx"
    session = Dialogue()
    main.route_command(
        f"добавь в документ «{path}» Привет, мир!", dialogue=session, typed=True
    )
    assert session.capability and not path.exists()
    main.route_command("да", dialogue=session, typed=True)
    assert "Привет, мир!" in docs.read(str(path))


def test_unquoted_word_request_and_profile_are_not_notes(tmp_path):
    path = tmp_path / "отчёт.docx"
    session = Dialogue()
    main.route_command(
        f"Запиши в документ {path} что встреча завтра", dialogue=session, typed=True
    )
    assert session.capability.action == "word_append"
    main.route_command("да", dialogue=session, typed=True)
    assert "встреча завтра" in docs.read(str(path))
    main.route_command(
        "запомни обо мне мой любимый цвет зелёный", dialogue=session, typed=True
    )
    assert "зелёный" in memory_store.recall("цвет")
    assert features.read_notes() == []


def test_observer_blocks_document_write(tmp_path, monkeypatch):
    monkeypatch.setitem(settings._current, "mode", "Наблюдатель")
    path = tmp_path / "note.txt"
    session = Dialogue()
    invoke(session, "write_text", {"path": str(path), "text": "text"})
    assert session.capability is None and not path.exists()


def test_profile_persists_and_forget_needs_confirmation():
    session = Dialogue()
    tools.request(session, "profile_remember", "Мой любимый цвет зелёный")
    assert "зелёный" in memory_store.recall("цвет")
    tools.request(session, "profile_forget")
    assert memory_store.recall()
    tools.confirmation(session, "да")
    assert not memory_store.recall()


def test_persistent_conversation_obeys_history_setting(monkeypatch):
    memory_store.exchange("Привет", "Здравствуй")
    assert len(memory_store.recent()) == 2
    monkeypatch.setitem(settings._current, "save_history", False)
    assert memory_store.recent() == []
    memory_store.exchange("Секрет", "Ответ")
    monkeypatch.setitem(settings._current, "save_history", True)
    assert len(memory_store.recent()) == 2


def test_cancel_all_timers_updates_persistent_store(monkeypatch):
    monkeypatch.setattr(features, "_timers", [])
    features.add_timer("Чай", 60)
    features.handle("отмени таймеры", "отмени таймеры")
    features.load_timers()
    assert features.remaining() == []


def test_browser_url_rejects_executable_schemes():
    import browser_tools

    for url in (
        "javascript:alert(1)",
        "file:///C:/Windows/x.exe",
        "https://user:password@example.com",
        "http://",
    ):
        with pytest.raises(ValueError):
            browser_tools.valid_url(url)
    assert browser_tools.valid_url("example.com") == "https://example.com"


def test_pause_phrase_never_starts_another_track(monkeypatch):
    import music_tools

    play = Mock()
    control = Mock(return_value="Музыка на паузе.")
    monkeypatch.setattr(music_tools, "play", play)
    monkeypatch.setattr(music_tools, "control", control)
    main.route_command("поставь музыку на паузу", dialogue=Dialogue(), typed=True)
    play.assert_not_called()
    control.assert_called_once_with("pause")
