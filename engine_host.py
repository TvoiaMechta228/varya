"""JSON-lines мост между WinUI и изолированными исполнителями Вари."""

import json
import logging
import os
from queue import Empty, Queue
import sys
import threading
import time

_wire = sys.stdout
_wire.reconfigure(encoding="utf-8", errors="backslashreplace")
sys.stdin.reconfigure(encoding="utf-8", errors="strict")
sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
sys.stdout = sys.stderr
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "2")

# Загрузка до COM предотвращает зависание инициализации библиотек в Windows.
import numpy  # noqa: F401
from piper import PiperVoice  # noqa: F401
import comtypes
import config
import conversation
from dialogue import Dialogue
import features
import local_brain
import main
import missions
import offline_speech
import browser_tools
import settings
import speech
import workspace_tasks
from tray import Tray

_write_lock = threading.Lock()
_commands = Queue()
_cancel = threading.Event()
_closing = threading.Event()
_background = threading.Event()
_alarms = Queue()


def emit(kind: str, value="") -> None:
    """Передать одно сообщение без смешивания потоков и диагностических выводов."""
    with _write_lock:
        _wire.write(
            json.dumps({"kind": kind, "value": value}, ensure_ascii=True) + "\n"
        )
        _wire.flush()


def receive() -> None:
    """Принимать остановку независимо от выполнения текущей команды."""
    try:
        for line in sys.stdin:
            if len(line) > 16000:
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict) or not isinstance(
                    request.get("op"), str
                ):
                    continue
            except (ValueError, TypeError):
                continue
            if request["op"] in {"stop", "close"}:
                _cancel.set()
                browser_tools.abort()
            if request["op"] == "background":
                (_background.set if request.get("value") else _background.clear)()
                continue
            if request["op"] == "close":
                _closing.set()
            _commands.put(request)
    finally:
        _closing.set()
        _cancel.set()
        _commands.put({"op": "close"})


def snapshot() -> None:
    """Передать настройки и задачи из единственного источника данных."""
    emit("settings", settings.snapshot())
    emit(
        "timers",
        [{"name": name, "seconds": seconds} for name, seconds in features.remaining()],
    )
    emit("notes", features.read_notes()[-50:])
    import planning

    emit("plans", planning.pending())
    import mail_tools

    if (settings.DATA_DIR / "mail-profile.dat").is_file():
        try:
            emit(
                "mail_account",
                {
                    key: value
                    for key, value in mail_tools.profile().items()
                    if key != "password"
                },
            )
        except ValueError as error:
            emit("notice", str(error))


def clock() -> None:
    """Обновлять таймеры независимо от записи речи или ожидания модели."""
    import psutil

    process = psutil.Process()
    ticks = 0
    while not _closing.wait(1):
        try:
            for name in features.expired_names():
                emit("timer", name)
                _alarms.put(name)
            emit("timers", [{"name": n, "seconds": s} for n, s in features.remaining()])
            local_brain.release_idle()
            ticks += 1
            if ticks % 5 == 0:
                processes = [process, *process.children(recursive=True)]
                memory = 0
                for child in processes:
                    try:
                        memory += child.memory_info().rss
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                emit("resources", memory)
        except (OSError, ValueError):
            if _closing.is_set():
                return


def run() -> None:
    """Сохранить последовательность действий, COM-поток и одноразовые подтверждения."""
    comtypes.CoInitialize()
    os.environ["VARYA_NATIVE"] = "1"
    settings.load()
    options = settings.snapshot()
    if options["voice"] not in {"piper", "sapi"}:
        options["voice"] = "piper"
    options.update(recognition="vosk", backend="llamacpp", model="qwen2.5-1.5b-local")
    settings.save(options)
    main.setup_logging()
    config.PHRASE_TIME_LIMIT = 20
    conversation.enabled = True
    speech.cancel_event, speech.shutdown_event = _cancel, _closing

    def interrupt_voice():
        _cancel.set()
        _commands.put({"op": "stop"})
        emit("notice", "Остановила ответ по голосовой команде.")

    speech.barge_in_callback = interrupt_voice

    def publish(kind, value):
        nonlocal listening
        if kind == "mic_unavailable":
            listening = False
        if kind in {"user", "assistant"}:
            missions.audit("Запрос" if kind == "user" else "Ответ", value)
        emit(kind, value)

    listening = False
    token = speech.transcript.set(publish)
    warning = features.load_timers()
    if warning:
        emit("notice", warning)
    if warning is None:
        features.save_timers()
    session = Dialogue()
    version = 0

    class TrayEvents:
        def put(self, message):
            emit(*message)

    tray = Tray(TrayEvents())
    threading.Thread(target=receive, daemon=True, name="NativeInput").start()
    threading.Thread(target=clock, daemon=True, name="NativeClock").start()
    emit("ready", {"version": config.ASSISTANT_VERSION, "offline": True, "data": str(settings.DATA_DIR)})
    snapshot()
    last_tick = 0.0
    try:
        while not _closing.is_set():
            speech.barge_in_enabled = listening
            if time.monotonic() - last_tick >= 1:
                while not _alarms.empty():
                    speech.speak("Время вышло. " + _alarms.get_nowait())
                emit("tray", tray.ready)
                last_tick = time.monotonic()
            try:
                request = _commands.get(timeout=0 if listening else 0.2)
            except Empty:
                if not listening:
                    continue
                request = {"op": "voice"}
            op = request["op"]
            if op == "close":
                break
            if op == "stop":
                listening = False
                session = Dialogue()
                version += 1
                offline_speech.release_microphone()
                _cancel.clear()
                emit("choices", {"version": version, "items": []})
                emit("idle", {"listening": False})
                continue
            if _cancel.is_set():
                continue
            try:
                if op == "start":
                    listening = True
                    speech.new_session()
                    emit("listening", True)
                    continue
                if op == "devices":
                    emit("devices", speech.sr.Microphone.list_microphone_names())
                elif op == "settings":
                    incoming = request.get("values", {})
                    if isinstance(incoming, dict):
                        settings.save(
                            {
                                **settings.snapshot(),
                                **incoming,
                                "recognition": "vosk",
                                "backend": "llamacpp",
                            }
                        )
                        features.save_timers()
                        speech.new_session()
                        session = Dialogue()
                        version += 1
                        emit("choices", {"version": version, "items": []})
                        emit("notice", "Настройки сохранены")
                        snapshot()
                elif op == "mail_config":
                    import mail_tools

                    account = mail_tools.configure(request.get("values", {}))
                    session = Dialogue()
                    version += 1
                    emit("choices", {"version": version, "items": []})
                    emit("mail_account", account)
                    emit(
                        "notice",
                        "Почтовый профиль зашифрован и сохранён. Проверить вход можно запросом непрочитанных писем.",
                    )
                elif op == "snapshot":
                    snapshot()
                elif op == "history":
                    emit("history", workspace_tasks.history())
                elif op == "preview":
                    speech.speak("Привет! Я Варя. Готова помочь тебе с компьютером.")
                elif op in {"typed", "voice", "choice"}:
                    text = request.get("text", "")
                    if op == "voice":
                        text = speech.listen()
                    if not text:
                        continue
                    if not isinstance(text, str) or len(text) > 2000:
                        emit("notice", "Допустимо сообщение до 2000 символов")
                        continue
                    if op == "choice" and request.get("version") != version:
                        emit("notice", "Этот вопрос уже закрыт. Повтори просьбу.")
                        continue
                    version += 1
                    quiet = speech.quiet_reply.set(
                        op != "voice" and not settings.snapshot()["speak_typed"]
                    )
                    try:
                        emit("busy", True)
                        if not main.route_command(
                            text,
                            dialogue=session,
                            typed=op != "voice",
                            require_wake=_background.is_set(),
                        ):
                            emit("exit")
                            break
                    finally:
                        speech.quiet_reply.reset(quiet)
                    choices = []
                    if session.mission or session.proposal or session.capability:
                        choices = ["Да", "Нет"]
                    elif session.pending == "duration":
                        choices = ["На пять минут", "На четверть часа", "На полчаса"]
                    elif session.pending == "кнопки выбранного окна":
                        choices = [
                            str(i + 1) for i in range(len(session.window_choices))
                        ]
                    emit("choices", {"version": version, "items": choices})
                    snapshot()
            except Exception as error:
                logging.getLogger(__name__).exception("Ошибка операции %s", op)
                emit("notice", "Не удалось завершить операцию. " + str(error)[:250])
                if op == "voice":
                    listening = False
            finally:
                if not _cancel.is_set():
                    emit("idle", {"listening": listening})
    finally:
        _closing.set()
        _cancel.set()
        local_brain.close()
        import browser_tools

        browser_tools.close()
        import music_tools

        music_tools.close()
        tray.close()
        offline_speech.release_microphone()
        speech.close()
        speech.transcript.reset(token)
        comtypes.CoUninitialize()


if __name__ == "__main__":
    try:
        run()
    except Exception:
        logging.exception("Движок не запущен")
        emit("fatal", "Не удалось запустить движок. Проверь журнал и папку приложения.")
