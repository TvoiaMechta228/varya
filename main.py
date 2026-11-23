"""Точка входа, настройка журнала и маршрутизация голосовых команд."""

from datetime import datetime
import logging
import missions
from logging.handlers import RotatingFileHandler
import os
import re
import subprocess
import time

import actions
import config
import conversation
import desktop
from dialogue import Dialogue
import command_results
import requests_pc
import features
import settings
from safety import normalize_text
import speech

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Подключить UTF-8 журнал с ротацией, не сохраняя аудио."""
    config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        config.LOG_FILE, maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def parse_search_query(text: str) -> str | None:
    """Выделить запрос, сохранив его регистр и значимую пунктуацию."""
    match = re.fullmatch(
        r"найди\s+в\s+интернете(?:\s+(.*))?", text.strip(), re.IGNORECASE,
    )
    if match is None:
        return None
    return (match.group(1) or "").strip()


def route_command(text: str, *, dialogue: Dialogue | None = None,
                  typed: bool = False, require_wake: bool = False) -> bool:
    """Выполнить команду; False означает завершение ассистента."""
    heard_at = time.monotonic()
    prefix = re.match(r"^(?:варя|джарвис|jarvis)(?:\s*[,!:.]\s*|\s+|$)", text.strip(), re.I)
    if prefix:
        if not typed:
            speech.publish("wake", "")
        text = text.strip()[prefix.end():].strip()
        if not text:
            speech.speak("Да, я слушаю.")
            return True
    elif (not typed and (require_wake or settings.snapshot()["wake_word"])
          and normalize_text(text) not in {"стоп", "выход", "пока"}):
        if not (dialogue and ((dialogue.pending and heard_at < dialogue.until)
                              or ((dialogue.mission or dialogue.proposal) and heard_at < (dialogue.mission or dialogue.proposal).expires
                                  and normalize_text(text) in requests_pc.REPLIES))):
            return True
    original = text
    if len(text) > 2000:
        speech.speak("Раздели просьбу на сообщения до 2000 символов.")
        return True
    speech.publish("user", text)
    command = normalize_text(text)
    try:
        if command in {"стоп", "выход", "пока"}:
            speech.speak("До свидания!")
            return False
        session = dialogue if dialogue is not None else Dialogue()
        if speech.is_cancelled():
            return True
        if missions.mode() == "Наблюдатель":
            session.pending = ""
            session.mission = session.proposal = None
            kind = missions.identify(command)
            if kind in missions.READ_ONLY:
                speech.speak(missions.request(session, kind).text)
            elif command in {"мои таймеры", "мои заметки", "что ты умеешь", "сколько осталось"}:
                features.handle(command, text)
            elif command in {"список окон", "какие окна открыты"}:
                speech.speak(desktop.execute(command, text))
            elif conversation.enabled and requests_pc.ordinary_question(text):
                conversation.reply(text)
            else:
                speech.speak("В режиме наблюдателя доступны диагностика, просмотр и вопросы. Для управления выбери «Помощник» в настройках.")
            return True
        confirmed = missions.confirm(session, command)
        if confirmed is None:
            confirmed = requests_pc.confirmation(session, command)
        if confirmed is not None:
            if confirmed.card:
                speech.publish(confirmed.card, confirmed.value)
            session.last_request, session.last_result = original, confirmed.text
            speech.speak(confirmed.text)
            return True
        direct_kind = None if session.pending else missions.identify(command)
        text, question = session.prepare(text, now=heard_at)
        if question:
            speech.speak(question)
            return True
        command = normalize_text(text)
        if speech.is_cancelled():
            return True

        def answer(message: str) -> None:
            session.last_request = original
            session.last_result = message
            speech.speak(message)

        def present(result: command_results.Result) -> None:
            if result.card:
                speech.publish(result.card, result.value)
            if result.text and not speech.is_cancelled():
                answer(result.text)

        kind = direct_kind or missions.identify(command)
        result = missions.request(session, kind) if kind else command_results.execute(session, command, text)
        if result is None:
            result = missions.sequence_request(session, text)
        if result is None:
            result = requests_pc.direct(session, command, text)
        if result:
            present(result)
            return True
        if command in {"забудь разговор", "режим команд", "закончи разговор"}:
            session.last_request = session.last_result = session.selected_timer = session.selected_note = ""
            session.selected_window = session.button_target = None
            session.buttons = ()
        if conversation.control(command):
            return True
        response = desktop.execute(command, text)
        if response is not None:
            answer(response)
            return True
        if features.handle(command, text):
            return True
        query = parse_search_query(text)
        if query is not None:
            if query:
                actions.search_web(query)
                answer(f"Открываю поиск Яндекса по запросу «{query}».")
            else:
                speech.speak(
                    "Произнесите запрос после слов: найди в интернете.",
                )
        elif command.startswith("открой "):
            app = command.removeprefix("открой ")
            if actions.open_app(app):
                answer(f"Открываю {app}.")
            elif settings.snapshot().get("installed_apps") and actions.open_installed(app):
                answer(f"Открываю {app}.")
            elif dialogue is not None and conversation.enabled and len(app.split()) > 2:
                proposal = requests_pc.understand(session, original)
                if proposal is not None:
                    present(proposal)
                else:
                    answer("Какую программу или папку открыть?")
            elif settings.snapshot().get("installed_apps"):
                answer("Назови точное имя приложения в меню Пуск.")
            else:
                answer("Этой программы нет в белом списке.")
        elif command in {"который час", "сколько времени"}:
            speech.speak(f"Сейчас {datetime.now():%H:%M}.")
        elif command == "выключи компьютер":
            if actions.shutdown_pc():
                speech.speak("Выключение через десять секунд.")
        elif command == "перезагрузи компьютер":
            if actions.reboot_pc():
                speech.speak("Перезагрузка через десять секунд.")
        elif command == "отмени выключение":
            actions.cancel_shutdown()
            speech.speak("Запланированное выключение отменено.")
        elif conversation.enabled:
            result = requests_pc.understand(session, original) if dialogue is not None else None
            if result is None:
                conversation.reply(original, context=(session.last_request, session.last_result))
            else:
                present(result)
        else:
            speech.speak("Команда не распознана.")
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.exception("Не удалось выполнить команду")
        speech.speak("Не удалось выполнить команду. Подробности в журнале.")
    except Exception:
        logger.exception("Непредвиденная ошибка действия")
        speech.speak("Произошла ошибка. Попробуйте ещё раз.")
    return True


def main() -> None:
    """Слушать микрофон до команды выхода или Ctrl+C."""
    if os.name != "nt":
        print("Варя предназначена для Windows 10/11.")
        return
    settings.load()
    try:
        setup_logging()
    except OSError as error:
        print(f"Не удалось открыть журнал: {error}")
        return
    try:
        timer_warning = features.load_timers()
        if timer_warning:
            speech.speak(timer_warning)
        speech.speak(f"Я {config.ASSISTANT_NAME}. Слушаю ваши команды.")
        dialogue = Dialogue()
        while True:
            for name in features.expired_names():
                speech.speak(f"Время вышло. {name}.")
            text = speech.listen()
            if text is not None and not route_command(text, dialogue=dialogue):
                break
    except KeyboardInterrupt:
        logger.info("Остановка с клавиатуры")
        speech.speak("До свидания!")


if __name__ == "__main__":
    main()
