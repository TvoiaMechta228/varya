"""Сценарии взаимодействия списка дел и голосовых команд."""

import tkinter as tk
from tkinter import ttk

import features
import main
import settings
from organizer import Organizer


def test_organizer_and_voice_share_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(features, "_timers", [])
    root = tk.Tk()
    root.withdraw()
    try:
        tabs = ttk.Notebook(root)
        panel = Organizer(tabs)
        main.route_command("Варя, таймер чай на три минуты")
        panel.name.insert(0, "Перерыв")
        panel.start_timer()
        assert set(panel.timers.get_children()) == {"чай", "Перерыв"}
        main.route_command("отмени таймер чай")
        panel.refresh()
        assert panel.timers.get_children() == ("Перерыв",)
        panel.timers.selection_set("Перерыв")
        panel.cancel()
        assert not features.remaining()
        main.route_command("запомни Купить хлеб")
        panel.note.insert(0, "Позвонить домой")
        panel.save_note()
        panel.query.set("ХЛЕБ")
        assert panel.results.get("1.0", "end-1c") == "Купить хлеб"
    finally:
        root.destroy()


def test_names_and_expiration(monkeypatch):
    monkeypatch.setattr(features, "_timers", [])
    monkeypatch.setattr(features.time, "monotonic", lambda: 100)
    main.route_command("поставь таймер чай на пять секунд")
    main.route_command("таймер чай на минуту")
    main.route_command("таймер на две минуты")
    assert len(features.remaining()) == 2
    monkeypatch.setattr(features.time, "monotonic", lambda: 106)
    assert features.expired_names() == ["чай"]
    assert features.expired_names() == []
    assert len(features.remaining()) == 1
