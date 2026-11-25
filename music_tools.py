"""Локальная музыка с проверяемым состоянием собственного Windows-плеера."""

from pathlib import Path
import asyncio
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextvars import copy_context
from queue import Empty, Queue
import threading
import time

import settings
import speech

_player = None
_thread = None
_jobs = Queue()
_lock = threading.Lock()


def _worker() -> None:
    """Держать плеер в одном STA-потоке и обслуживать его оконные сообщения."""
    import pythoncom

    pythoncom.CoInitialize()
    try:
        while True:
            pythoncom.PumpWaitingMessages()
            try:
                function, argument, future, context = _jobs.get(timeout=0.03)
            except Empty:
                continue
            if function is None:
                future.set_result(None)
                return
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(context.run(function, argument))
            except Exception as error:
                future.set_exception(error)
    finally:
        _close_player()
        pythoncom.CoUninitialize()


def _invoke(function, argument: str) -> str:
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_worker, name="VaryaMusic", daemon=True)
            _thread.start()
    future = Future()
    _jobs.put((function, argument, future, copy_context()))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if speech.is_cancelled():
            future.cancel()
            raise InterruptedError
        try:
            return future.result(timeout=0.1)
        except FutureTimeout:
            continue
    raise OSError("Плеер не ответил за 30 секунд")


def player():
    global _player
    if _player is None:
        from win32com.client import Dispatch

        try:
            _player = Dispatch("WMPlayer.OCX")
            _player.settings.autoStart = False
        except Exception as error:
            raise ValueError(
                "В Windows недоступен компонент Windows Media Player. Включи компонент мультимедиа или открой музыкальный сервис."
            ) from error
    return _player


def music_files(query: str = "") -> list[Path]:
    from win32com.shell import shell, shellcon

    root = Path(
        settings.snapshot().get("music_root")
        or shell.SHGetFolderPath(0, shellcon.CSIDL_MYMUSIC, 0, 0)
    )
    if not root.is_dir():
        raise ValueError("Папка музыки не найдена. Выбери её в настройках.")
    result = []
    visited = 0
    for path in root.rglob("*"):
        if speech.is_cancelled():
            raise InterruptedError
        visited += 1
        if path.is_file() and path.suffix.casefold() in {
            ".mp3",
            ".wav",
            ".wma",
            ".flac",
            ".m4a",
        }:
            if not query or query.casefold() in path.stem.casefold():
                result.append(path)
        if len(result) >= 200 or visited >= 20000:
            break
    return sorted(result)


def play(query: str = "") -> str:
    """Запустить свою музыку и проверить фактическое состояние воспроизведения."""
    return _invoke(_play, query)


def _play(query: str) -> str:
    import pythoncom

    tracks = music_files(query)
    if not tracks:
        raise ValueError(
            "Подходящих аудиофайлов в папке музыки нет. Выбери папку с музыкой в настройках или открой музыкальный сервис."
        )
    owner = player()
    playlist = owner.newPlaylist("Варя", "")
    for track in tracks:
        playlist.appendItem(owner.newMedia(str(track)))
    owner.currentPlaylist = playlist
    owner.controls.play()
    for _ in range(30):
        pythoncom.PumpWaitingMessages()
        if speech.is_cancelled():
            owner.controls.stop()
            raise InterruptedError
        if owner.playState == 3:
            return "Играет «" + str(owner.currentMedia.name) + "»."
        time.sleep(0.1)
    raise OSError(
        "Плеер получил файл, но начало воспроизведения не подтверждено. Проверь формат и устройство вывода."
    )


def control(action: str) -> str:
    """Передать медиакоманду потоку плеера или активной системной медиасессии."""
    return _invoke(_control, action)


def _control(action: str) -> str:
    import pythoncom

    if _player is None:
        return asyncio.run(system_control(action))
    if action == "pause":
        _player.controls.pause()
        for _ in range(20):
            pythoncom.PumpWaitingMessages()
            if _player.playState == 2:
                return "Музыка на паузе."
            if speech.is_cancelled():
                raise InterruptedError
            time.sleep(0.05)
        raise OSError("Плеер не подтвердил паузу. Проверь его состояние.")
    if action == "resume":
        _player.controls.play()
    elif action == "next":
        _player.controls.next()
    elif action == "previous":
        _player.controls.previous()
    return (
        "Текущий трек — «" + str(_player.currentMedia.name) + "»."
        if _player.currentMedia
        else "Текущего трека нет."
    )


async def system_control(action: str) -> str:
    """Использовать системные медиасессии браузеров и плееров с подтверждением состояния."""
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as Manager,
    )
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
    )

    manager = await asyncio.wait_for(Manager.request_async(), timeout=4)
    session = manager.get_current_session()
    if session is None:
        if action != "status":
            raise ValueError(
                "Windows не показывает активного плеера. Сначала включи воспроизведение."
            )
        return "Windows не показывает активного плеера. Включи локальную музыку или открой воспроизведение на сайте."
    operations = {
        "pause": session.try_pause_async,
        "resume": session.try_play_async,
        "next": session.try_skip_next_async,
        "previous": session.try_skip_previous_async,
    }
    if action in operations:
        if not await asyncio.wait_for(operations[action](), timeout=4):
            raise OSError("Плеер отклонил медиакоманду")
        expected = Status.PAUSED if action == "pause" else Status.PLAYING
        if action in {"pause", "resume"}:
            for _ in range(20):
                if session.get_playback_info().playback_status == expected:
                    break
                await asyncio.sleep(0.1)
            else:
                raise OSError(
                    "Команда передана плееру, но изменение состояния не подтверждено Windows."
                )
    properties = await asyncio.wait_for(
        session.try_get_media_properties_async(), timeout=4
    )
    state = session.get_playback_info().playback_status
    return (
        (
            "Пауза"
            if state == Status.PAUSED
            else "Воспроизведение"
            if state == Status.PLAYING
            else "Плеер"
        )
        + " — «"
        + (properties.title or "без названия")
        + "»."
    )


def _close_player() -> None:
    global _player
    if _player is not None:
        _player.controls.stop()
        _player.close()
        _player = None


def close() -> None:
    """Закрыть только собственный плеер в его COM-потоке."""
    global _thread
    if _thread is None:
        return
    future = Future()
    _jobs.put((None, "", future, None))
    try:
        future.result(timeout=3)
    except FutureTimeout:
        return
    _thread.join(timeout=3)
    if not _thread.is_alive():
        _thread = None
