"""Дополнительные команды без запуска произвольного кода."""

from datetime import datetime
from decimal import Decimal
import json
import math
import logging
from math import ceil
from threading import RLock
import re
import time

import actions
import settings
import speech

logger = logging.getLogger(__name__)
_timers: list[tuple[str, float]] = []
_timer_lock = RLock()
_note_lock = RLock()


def save_timers() -> None:
    """Сохранить сроки в календарном времени для восстановления после перезапуска."""
    with _timer_lock:
        now, wall = time.monotonic(), time.time()
        data = [{"name": name, "deadline": wall + end - now} for name, end in _timers]
        try:
            settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
            path = settings.DATA_DIR / "timers.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(path)
            import reminder_schedule
            message = reminder_schedule.refresh([item["deadline"] for item in data])
            if message:
                speech.publish("notice", message)
        except OSError:
            logger.exception("Не удалось сохранить таймеры")
            speech.publish("notice", "Таймеры работают, но сохранить их для перезапуска не удалось.")


def load_timers() -> str | None:
    """Восстановить таймеры; пропущенные сигналы сообщить после запуска Вари."""
    global _timers
    path = settings.DATA_DIR / "timers.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or len(data) > 10:
            raise ValueError("Некорректный список таймеров")
        timers = []
        names = set()
        now, wall = time.monotonic(), time.time()
        for item in data:
            name, end = item["name"], item["deadline"]
            if (not isinstance(name, str) or not 1 <= len(name) <= 48
                    or name.casefold() in names or type(end) not in (float, int)
                    or not math.isfinite(end) or end - wall > 86400):
                raise ValueError("Некорректный таймер")
            names.add(name.casefold())
            timers.append((name, now + min(86400, end - wall)))
        with _timer_lock:
            _timers = timers
    except FileNotFoundError:
        return
    except (OSError, ValueError, KeyError, TypeError):
        logger.exception("Не удалось восстановить таймеры")
        return "Не удалось прочитать сохранённые таймеры. Проверь файл timers.json в папке данных."
    return None
HELP = (
    "Джарвис, проверь компьютер • обслуживание ПК • проверь обновления программ\n"
    "Прочитай экран • прочитай выбранное окно • покажи непрочитанные письма\n"
    "Создай архив проекта • запусти тесты проекта • создай черновик письма\n"
    "Наведи порядок на рабочем столе • отмени сортировку • продолжи сортировку\n"
    "Подготовь меня к работе • проверь проект • календарь на сегодня • открой OneDrive\n"
    "Открой блокнот, затем открой документы, потом сделай скриншот\n"
    "Создай приложение для учёта расходов • создай интернет-магазин\n"
    "Покажи загрузки • открой настройки экрана • покажи кнопки в окне Калькулятор\n"
    "Давай поговорим • забудь разговор • режим команд\n"
    "Список окон • переключись на окно Блокнот • закрой окно Блокнот\n"
    "Открой документы • создай папку Планы • запусти приложение Paint\n"
    "Напечатай <текст> — в активном документе Блокнота\n"
    "Где курсор • курсор на 400 300 • левый клик • прокрути вниз\n"
    "Варя, который час • какая сегодня дата\n"
    "Открой блокнот / калькулятор / paint / браузер\n"
    "Найди в интернете рецепт борща • скриншот\n"
    "Громче • тише • переключи звук • пауза музыки\n"
    "Сверни окно • разверни окно • покажи рабочий стол\n"
    "Таймер чай на три минуты • отмени таймер чай • мои таймеры\n"
    "Таймер на пять минут • сколько осталось • отмени таймеры\n"
    "Запомни купить молоко • мои заметки\n"
    "Посчитай 12 плюс 3 • что ты умеешь\n"
    "Выключи компьютер / перезагрузи компьютер (с подтверждением)\n"
    "Отмени выключение • стоп / выход / пока"
)


def duration(text: str) -> int | None:
    """Разобрать длительность 1–86400 секунд с русскими числительными."""
    if text.strip() == "четверть часа":
        return 900
    if text.strip() in {"полчаса", "пол часа", "половину часа"}:
        return 1800
    match = re.fullmatch(
        r"(.+?)\s+(секунд[ауы]?|минут[ауы]?|час(?:а|ов)?)", text.strip(),
    )
    if text.strip() in {"секунду", "минуту", "час"}:
        return {"секунду": 1, "минуту": 60, "час": 3600}[text.strip()]
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


def duration_words(seconds: int) -> str:
    """Прочитать остаток времени с правильными русскими окончаниями."""
    hours, rest = divmod(seconds, 3600)
    minutes, rest = divmod(rest, 60)
    parts = []
    for count, forms in ((hours, ("час", "часа", "часов")),
                         (minutes, ("минута", "минуты", "минут")),
                         (rest, ("секунда", "секунды", "секунд"))):
        if count:
            last = count % 10 if count % 100 not in range(11, 15) else 0
            word = forms[0] if last == 1 else forms[1] if 2 <= last <= 4 else forms[2]
            parts.append(f"{count} {word}")
    return " ".join(parts) or "0 секунд"


def due_timers() -> int:
    """Удалить истёкшие таймеры и вернуть их количество."""
    return len(expired_names())


def expired_names() -> list[str]:
    """Забрать завершённые таймеры ровно один раз."""
    with _timer_lock:
        now = time.monotonic()
        names = [name for name, end in _timers if end <= now]
        _timers[:] = [(name, end) for name, end in _timers if end > now]
        if names:
            save_timers()
        return names


def remaining() -> list[tuple[str, int]]:
    """Снимок таймеров в порядке срабатывания."""
    with _timer_lock:
        now = time.monotonic()
        return [(name, max(0, ceil(end - now))) for name, end in
                sorted(_timers, key=lambda timer: timer[1])]


def add_timer(name: str, seconds: int) -> str:
    """Запустить таймер с уникальным названием."""
    with _timer_lock:
        if len(_timers) >= 10:
            raise ValueError("Уже работают десять таймеров.")
        if not 1 <= seconds <= 86400:
            raise ValueError("Допустимое время — от секунды до суток.")
        used = {label.casefold() for label, _ in _timers}
        name = " ".join(name.split())
        if not name:
            name = next(f"Таймер {i}" for i in range(1, 12)
                        if f"таймер {i}" not in used)
        if len(name) > 48 or name.casefold() in used:
            raise ValueError("Выберите свободное название до 48 символов.")
        _timers.append((name, time.monotonic() + seconds))
        save_timers()
        return name


def cancel_timer(name: str) -> bool:
    """Отменить только таймер с точным названием."""
    with _timer_lock:
        for timer in _timers:
            if timer[0].casefold() == name.casefold():
                _timers.remove(timer)
                save_timers()
                return True
    return False


def read_notes() -> list[dict]:
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
    with _note_lock:
        _append_note(text.strip())


def _append_note(text: str) -> None:
    if not 1 <= len(text) <= 1000:
        raise ValueError("Допустимо от 1 до 1000 символов")
    notes = read_notes()
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
        speech.publish("assistant", "Готово.")
    elif command in actions.WINDOW_KEYS:
        actions.window_control(command)
        speech.speak("Готово.")
    elif command.startswith("отмени таймер "):
        name = command.removeprefix("отмени таймер ")
        speech.speak(f"Таймер {name} отменён." if cancel_timer(name)
                     else "Такого таймера нет.")
    elif command == "мои таймеры":
        active = remaining()
        speech.speak(". ".join(f"{name}, осталось {duration_words(sec)}"
                               for name, sec in active)
                     if active else "Активных таймеров нет.")
    elif command == "отмени таймеры":
        with _timer_lock:
            _timers.clear()
            save_timers()
        speech.speak("Таймеры отменены.")
    elif command == "сколько осталось":
        active = remaining()
        if active:
            _, seconds = active[0]
            speech.speak(f"До ближайшего таймера {duration_words(seconds)}.")
        else:
            speech.speak("Активных таймеров нет.")
    elif command == "мои заметки":
        notes = read_notes()
        speech.speak("Последние заметки: " + "; ".join(
            note["text"] for note in notes[-5:]
        ) if notes else "Заметок пока нет.")
    elif command.startswith("посчитай "):
        speech.speak(calculate(raw.split(maxsplit=1)[1].lower()))
    else:
        return False
    return True
