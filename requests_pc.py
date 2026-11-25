"""Свободные просьбы, системные разделы и одношаговое подтверждение."""

import asyncio
from dataclasses import dataclass
import logging
import os
import re
import time

import aiohttp
from win32com.shell import shell, shellcon

import actions
import config
import conversation
import capabilities
from command_results import Result, timer_reference, window_action
from dialogue import Dialogue
import desktop
import features
import missions
import settings
import speech
from understanding import Intent, interpret, ordinary_question
import window_buttons


FOLDERS = {"загрузки": None, "документы": shellcon.CSIDL_PERSONAL,
           "изображения": shellcon.CSIDL_MYPICTURES, "рабочий стол": shellcon.CSIDL_DESKTOPDIRECTORY,
           "музыка": shellcon.CSIDL_MYMUSIC, "видео": shellcon.CSIDL_MYVIDEO}
PAGES = {"звук": "sound", "экран": "display", "bluetooth": "bluetooth",
         "сеть": "network-status", "приложения": "appsfeatures"}
_ALIASES = {"загрузок": "загрузки", "скачанные файлы": "загрузки", "загрузках": "загрузки",
            "звука": "звук", "экрана": "экран", "блютуз": "bluetooth", "сети": "сеть",
            "дисплея": "экран", "картинки": "изображения", "фотографии": "изображения",
            "приложений": "приложения", "документов": "документы", "музыки": "музыка"}


@dataclass(frozen=True)
class Proposal:
    """Показанное намерение и срок, в течение которого его можно подтвердить."""

    intent: Intent
    expires: float
    target: tuple[int, int, str] | None = None
    button: window_buttons.Button | None = None


def open_location(action: str, value: str) -> Result:
    """Открыть проверенный системный раздел или известную папку Windows."""
    key = _ALIASES.get(value.casefold(), value.casefold())
    if action == "settings" and key in PAGES:
        os.startfile("ms-settings:" + PAGES[key])
        return Result(f"Открываю настройки Windows — {key}.")
    if action == "folder" and key in FOLDERS:
        if key == "загрузки":
            path = shell.SHGetKnownFolderPath(shell.FOLDERID_Downloads)
        else:
            path = shell.SHGetFolderPath(0, FOLDERS[key], 0, 0)
        os.startfile(path)
        return Result(f"Открываю папку «{key.capitalize()}».")
    return Result("Уточни название. Доступны " + ", ".join(PAGES if action == "settings" else FOLDERS) + ".")


def perform(session: Dialogue, intent: Intent) -> Result:
    """Выполнить проверенную локальную операцию; аргументы остаются данными."""
    action, arg = intent.action, intent.argument
    if action in capabilities.DESCRIPTIONS:
        return capabilities.request(session, action, arg)
    if action in {"diagnose", "sort", "work", "check", "agenda"}:
        return missions.request(session, action)
    if action in {"folder", "settings"}:
        return open_location(action, arg)
    if action == "timer":
        if not intent.seconds:
            session.timer_name = arg
            return Result(session.ask("duration", "Через сколько напомнить?")[1])
        try:
            name = features.add_timer(arg, intent.seconds)
        except ValueError as error:
            return Result(str(error))
        session.selected_timer = name
        return Result(f"Таймер «{name}» запущен. Время — {features.duration_words(intent.seconds)}.", "timer_card", name)
    if action == "remaining":
        return timer_reference(session, "remaining", arg)
    if action == "timers":
        active = features.remaining()
        return Result(". ".join(f"{name} — {features.duration_words(sec)}" for name, sec in active) or "Таймеров пока нет.")
    if action == "notes":
        notes = features.read_notes()[-5:]
        return Result("\n".join(n["text"] for n in notes) or "Заметок пока нет.")
    if action == "note":
        if not arg:
            return Result(session.ask("запомни", "Что записать?")[1])
        features.remember(arg)
        session.selected_note = arg
        return Result("Записала.", "note_card", arg)
    if action == "app":
        if not arg:
            return Result(session.ask("открой", "Какое приложение открыть?")[1])
        arg = config.APP_ALIASES.get(arg.casefold(), arg.casefold())
        if arg in config.WEB_SHORTCUTS:
            actions.open_service(arg)
            return Result(f"Открываю {arg}.")
        if actions.open_app(arg) or (settings.snapshot().get("installed_apps") and actions.open_installed(arg.casefold())):
            return Result(f"Открываю {arg}.")
        return Result(f"«{arg}» нет среди разрешённых программ. Список есть в настройках Вари.")
    if action == "search":
        if not arg:
            return Result(session.ask("найди в интернете", "Что поискать?")[1])
        actions.search_web(arg)
        return Result(f"Открываю результаты по запросу «{arg}».")
    if action == "screenshot":
        path = actions.take_screenshot()
        return Result("Снимок сохранён.", "screenshot_card", str(path))
    if action in {"focus", "minimize", "maximize"}:
        verb = {"focus": "переключись на", "minimize": "сверни", "maximize": "разверни"}[action]
        if not arg:
            return Result(session.ask(f"{verb} окно", "Какое окно?")[1])
        return window_action(session, verb, arg)
    if action == "windows":
        names = [w[2] for w in desktop.windows()]
        return Result("\n".join(names[:12]) or "Открытых окон не вижу.")
    if action == "buttons":
        return Result(window_buttons.describe(session, arg))
    if action in {"louder", "quieter"}:
        actions.media_control("громче" if action == "louder" else "тише")
        return Result("Прибавила громкость." if action == "louder" else "Убавила громкость.")
    return Result("Такое действие я пока не умею выполнять. Могу показать открытые окна и доступные в них кнопки.")


YES = {"да", "подтверждаю", "сделай", "нажимай"}
NO = {"нет", "отмена", "отмени", "не надо"}
REPLIES = YES | NO


def confirmation(session: Dialogue, text: str) -> Result | None:
    """Одноразовый ответ относится только к показанному пользователю действию."""
    pending, session.proposal = session.proposal, None
    if pending is None:
        return None
    if text in NO:
        return Result("Отменила.")
    if text not in YES:
        return None
    if time.monotonic() > pending.expires or speech.is_cancelled():
        return Result("Подтверждение истекло. Попроси об этом ещё раз.")
    if pending.button is not None and pending.target is not None:
        return Result(window_buttons.invoke(pending.target, pending.button))
    return perform(session, pending.intent)


_PERIOD = r"(?P<period>полчаса|четверть часа|(?:(?:\w+ ){0,2})(?:минут[ауы]?|секунд[ауы]?|час(?:а|ов)?))"
REMINDERS = (
    re.compile(r"(?:напомни(?: мне)?|напомни мне пожалуйста) через " + _PERIOD + r"[, ]+(?P<task>.+)", re.I),
    re.compile(r"через " + _PERIOD + r"[, ]+напомни(?: мне)? (?P<task>.+)", re.I),
    re.compile(r"напомни(?: мне)? (?P<task>.+?) через " + _PERIOD, re.I),
)


def direct(session: Dialogue, command: str, raw: str = "") -> Result | None:
    """Локальные команды работают и при выключенной Ollama."""
    if command in REPLIES:
        return Result("Сейчас подтверждать нечего. Что сделать?")
    if command.startswith("кнопки выбранного окна "):
        return Result(window_buttons.select_from_list(session, command.removeprefix("кнопки выбранного окна ")))
    # Название напоминания остаётся текстом, даже если содержит слова другой команды.
    for pattern in REMINDERS:
        match = pattern.fullmatch((raw or command).strip(" .!?"))
        if match:
            seconds = features.duration(match["period"].lower())
            if seconds:
                return perform(session, Intent("timer", match["task"].strip(" ,"), seconds))
    volume = re.fullmatch(
        r"(?:(?:сделай|сделай звук|сделай музыку|можно) )?(?:(?:чуть|немного) )?(потише|погромче)", command)
    if volume:
        return perform(session, Intent("quieter" if volume[1] == "потише" else "louder"))
    if match := re.fullmatch(r"(?:открой|покажи|перейди в) (?:папку )?(.+)", command):
        value = _ALIASES.get(match[1], match[1])
        app = config.APP_ALIASES.get(value, value)
        if app in config.APP_WHITELIST or app in config.WEB_SHORTCUTS:
            return perform(session, Intent("app", app))
        if value in FOLDERS:
            return open_location("folder", value)
        if page := re.fullmatch(r"(?:настройки|параметры) (.+)", value):
            return open_location("settings", page[1])
    if match := re.fullmatch(r"(?:покажи |какие |список )?кнопки(?: (?:в окне|окна) (.+))?", command):
        return Result(window_buttons.describe(session, match[1] or ""))
    selection = re.fullmatch(r"(?:нажми|выбери)(?: кнопку| номер)? (.+)", command)
    if selection or (session.buttons and (command.isdigit() or command in window_buttons.NUMBERS)):
        if not session.buttons or session.button_target is None:
            return Result("Сначала скажи, в каком окне показать кнопки.")
        matches = window_buttons.choose(session.buttons, selection[1] if selection else command)
        if len(matches) != 1:
            return Result("Назови номер кнопки из показанного списка.")
        b = matches[0]
        session.proposal = Proposal(Intent("button"), time.monotonic() + 60, session.button_target, b)
        return Result(f"Нажать «{b.name}» в окне «{session.button_target[2]}»? Да или нет?")
    return None


def understand(session: Dialogue, text: str) -> Result | None:
    """Интерпретировать незнакомую реплику; None передаёт обычный вопрос в чат."""
    if ordinary_question(text) or len(text) > 2000:
        return None
    if conversation.memory_pressure():
        return Result(conversation.MEMORY_MESSAGE)
    try:
        speech.publish("state", "Разбираю просьбу…")
        intent = asyncio.run(interpret(text, session.last_result))
    except InterruptedError:
        return Result("")
    except (aiohttp.ClientError, OSError, TimeoutError, ValueError, KeyError, TypeError):
        logging.getLogger(__name__).warning("Не удалось разобрать просьбу", exc_info=True)
        return Result("Не удалось разобрать просьбу локальной моделью. Уточни действие и нужный файл или приложение.")
    if speech.is_cancelled():
        return Result("")
    if intent.action == "chat":
        return None
    if intent.action in {"note"}:
        labels = {"search": "Искать в Яндексе", "app": "Открыть приложение",
                  "note": "Сохранить заметку", "screenshot": "Сохранить снимок экрана",
                  "focus": "Переключиться на окно", "minimize": "Свернуть окно",
                  "maximize": "Развернуть окно"}
        if intent.argument or intent.action == "screenshot":
            session.proposal = Proposal(intent, time.monotonic() + 60)
            detail = f" «{intent.argument}»" if intent.argument else ""
            return Result(f"{labels[intent.action]}{detail}? Да или нет?")
    return perform(session, intent)
