"""Плеер и его закрытие выполняются в одном отдельном COM-потоке."""

import threading

import music_tools


def test_player_commands_and_shutdown_share_one_worker(monkeypatch):
    threads = []
    monkeypatch.setattr(music_tools, "_play", lambda query: str(threading.get_ident()))
    monkeypatch.setattr(
        music_tools, "_control", lambda action: str(threading.get_ident())
    )
    monkeypatch.setattr(
        music_tools, "_close_player", lambda: threads.append(threading.get_ident())
    )
    first = music_tools.play("тест")
    assert int(first) != threading.get_ident()
    assert music_tools.control("status") == first
    music_tools.close()
    assert threads == [int(first)]
    assert music_tools._thread is None
