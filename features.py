"""Дополнительные команды без запуска произвольного кода."""

from datetime import datetime
from decimal import Decimal
import json
import logging
import re
import time

import actions
import settings
import speech

logger = logging.getLogger(__name__)
_timers: list[float] = []
HELP = (
    "Варя, который час • какая сегодня дата\n"
    "Открой блокнот / калькулятор / paint / браузер\n"
    "Найди в интернете рецепт борща • скриншот\n"
    "Громче • тише • переключи звук • пауза музыки\n"
    "Сверни окно • разверни окно • покажи рабочий стол\n"
    "Таймер на пять минут • сколько осталось • отмени таймеры\n"
    "Запомни купить молоко • мои заметки\n"
    "Посчитай 12 плюс 3 • что ты умеешь\n"
    "Выключи компьютер / перезагрузи компьютер (с подтверждением)\n"
    "Отмени выключение • стоп / выход / пока"
)


def duration(text: str) -> int | None:
    """Разобрать длительность 1–86400 секунд с русскими числительными."""
    match = re.fullmatch(
        r"(.+?)\s+(секунд[ауы]?|минут[ауы]?|час(?:а|ов)?)", text.strip(),
    )
    if not match:
        return None
    number, unit = match.groups()
    words = {
        "один": 1, "одну": 1, "одна": 1, "две": 2, "два": 2,
        "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7,
        "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11,
        "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
        "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
        "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20,
        "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    }
    if number.isdigit():
        value = int(number)
    elif number in words:
        value = words[number]
    else:
        parts = number.split()
        if (len(parts) != 2 or words.get(parts[0], 0) < 20
                or not 1 <= words.get(parts[1], 0) <= 9):
            return None
        value = words[parts[0]] + words[parts[1]]
    multiplier = 1 if unit.startswith("сек") else (
        60 if unit.startswith("мин") else 3600
    )
    seconds = value * multiplier
    return seconds if 1 <= seconds <= 86400 else None


def due_timers() -> int:
    """Удалить истёкшие таймеры и вернуть их количество."""
    now = time.monotonic()
    due = sum(end <= now for end in _timers)
    _timers[:] = [end for end in _timers if end > now]
    return due


def _notes() -> list[dict]:
    """Загрузить локальные заметки с проверкой формата."""
    try:
        data = json.loads(
            (settings.DATA_DIR / "notes.json").read_text(encoding="utf-8"),
        )
    except FileNotFoundError:
        return []
    if not isinstance(data, list) or not all(
        isinstance(note, dict) and isinstance(note.get("text"), str)
        for note in data
    ):
        raise ValueError("Файл заметок повреждён")
    return data


def remember(text: str) -> None:
    """Добавить заметку в локальный JSON без изменения существующих записей."""
    if not 1 <= len(text) <= 1000:
        raise ValueError("Допустимо от 1 до 1000 символов")
    notes = _notes()
    if len(notes) >= 1000:
        raise ValueError("Достигнут предел в 1000 заметок")
    notes.append({"text": text, "created": datetime.now().isoformat()})
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = settings.DATA_DIR / "notes.tmp"
    temporary.write_text(
        json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    temporary.replace(settings.DATA_DIR / "notes.json")


def calculate(expression: str) -> str:
    """Вычислить одну арифметическую операцию без eval/exec."""
    match = re.fullmatch(
        r"(-?\d{1,12}(?:[.,]\d{1,6})?)\s*"
        r"(плюс|минус|умножить на|разделить на|[+*/−-])\s*"
        r"(-?\d{1,12}(?:[.,]\d{1,6})?)", expression,
    )
    if not match:
        return "Скажите, например: посчитай 12 плюс 3."
    left, operator, right = match.groups()
    a, b = Decimal(left.replace(",", ".")), Decimal(right.replace(",", "."))
    if operator in {"/", "разделить на"} and not b:
        return "На ноль делить нельзя."
    if operator in {"+", "плюс"}:
        result = a + b
    elif operator in {"-", "−", "минус"}:
        result = a - b
    elif operator in {"*", "умножить на"}:
        result = a * b
    else:
        result = a / b
    return format(result, ".6f").rstrip("0").rstrip(".").replace(".", ",")


def handle(command: str, raw: str) -> bool:
    """Выполнить известную дополнительную команду или вернуть False."""
    if command in {"что ты умеешь", "помощь", "список команд"}:
        logger.info("Команды:\n%s", HELP)
        speech.speak("Я умею открывать программы, искать в интернете, "
                     "делать скриншоты, ставить таймеры, сохранять заметки, "
                     "считать, управлять звуком и окнами.")
    elif command in {"какая сегодня дата", "какой сегодня день"}:
        weekdays = ("понедельник", "вторник", "среда", "четверг",
                    "пятница", "суббота", "воскресенье")
        now = datetime.now()
        speech.speak(f"Сегодня {now:%d.%m.%Y}, {weekdays[now.weekday()]}.")
    elif command in actions.MEDIA_KEYS:
        actions.media_control(command)
        logger.info("Выполнено: %s", command)
    elif command in actions.WINDOW_KEYS:
        actions.window_control(command)
        speech.speak("Готово.")
    elif command.startswith("таймер на "):
        seconds = duration(command.removeprefix("таймер на "))
        if seconds is None:
            speech.speak("Укажите время от одной секунды до 24 часов.")
        elif len(_timers) >= 10:
            speech.speak("Уже работают десять таймеров.")
        else:
            _timers.append(time.monotonic() + seconds)
            speech.speak(f"Таймер установлен на {seconds} секунд.")
    elif command == "отмени таймеры":
        _timers.clear()
        speech.speak("Таймеры отменены.")
    elif command == "сколько осталось":
        if _timers:
            seconds = max(0, round(min(_timers) - time.monotonic()))
            speech.speak(f"До ближайшего таймера {seconds} секунд.")
        else:
            speech.speak("Активных таймеров нет.")
    elif command.startswith("запомни "):
        remember(raw.split(maxsplit=1)[1])
        speech.speak("Заметка сохранена на этом компьютере.")
    elif command == "мои заметки":
        notes = _notes()
        speech.speak("Последние заметки: " + "; ".join(
            note["text"] for note in notes[-5:]
        ) if notes else "Заметок пока нет.")
    elif command.startswith("посчитай "):
        speech.speak(calculate(raw.split(maxsplit=1)[1].lower()))
    else:
        return False
    return True
