"""Точка входа, настройка журнала и маршрутизация голосовых команд."""

from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import subprocess

import actions
import config
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


def route_command(text: str) -> bool:
    """Выполнить команду; False означает завершение ассистента."""
    prefix = re.match(r"^варя(?:\s*[,!:.]\s*|\s+|$)", text.strip(), re.I)
    if prefix:
        text = text.strip()[prefix.end():].strip()
        if not text:
            speech.speak("Да, я слушаю.")
            return True
    elif (settings.snapshot()["wake_word"]
          and normalize_text(text) not in {"стоп", "выход", "пока"}):
        return True
    command = normalize_text(text)
    try:
        if command in {"стоп", "выход", "пока"}:
            speech.speak("До свидания!")
            return False
        if features.handle(command, text):
            return True
        query = parse_search_query(text)
        if query is not None:
            if query:
                actions.search_web(query)
                speech.speak("Открываю поиск Яндекса.")
            else:
                speech.speak(
                    "Произнесите запрос после слов: найди в интернете.",
                )
        elif command.startswith("открой "):
            if actions.open_app(command.removeprefix("открой ")):
                speech.speak("Открываю.")
            else:
                speech.speak("Этой программы нет в белом списке.")
        elif command in {"который час", "сколько времени"}:
            speech.speak(f"Сейчас {datetime.now():%H:%M}.")
        elif command == "скриншот":
            path = actions.take_screenshot()
            speech.speak(f"Скриншот сохранён: {path}.")
        elif command == "выключи компьютер":
            if actions.shutdown_pc():
                speech.speak("Выключение через десять секунд.")
        elif command == "перезагрузи компьютер":
            if actions.reboot_pc():
                speech.speak("Перезагрузка через десять секунд.")
        elif command == "отмени выключение":
            actions.cancel_shutdown()
            speech.speak("Запланированное выключение отменено.")
        else:
            speech.speak("Команда не распознана.")
    except (OSError, subprocess.SubprocessError, ValueError):
        logger.exception("Не удалось выполнить команду: %s", text)
        speech.speak("Не удалось выполнить команду. Подробности в журнале.")
    except Exception:
        logger.exception("Непредвиденная ошибка действия: %s", text)
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
        speech.speak(f"Я {config.ASSISTANT_NAME}. Слушаю ваши команды.")
        while True:
            for name in features.expired_names():
                speech.speak(f"Время вышло. {name}.")
            text = speech.listen()
            if text is not None and not route_command(text):
                break
    except KeyboardInterrupt:
        logger.info("Остановка с клавиатуры")
        speech.speak("До свидания!")


if __name__ == "__main__":
    main()
