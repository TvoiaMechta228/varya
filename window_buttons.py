"""Чтение кнопок одного окна через UI Automation и выбор пользователем."""

from dataclasses import dataclass
import re

import comtypes
import comtypes.client
import win32gui

import desktop
import safety
import speech


# Закрытие окна и питание ПК имеют собственные команды с голосовым подтверждением.
SYSTEM_CONTROLS = {"закрыть", "close", "завершение работы", "выключение", "перезагрузка",
                   "shut down", "restart", "выключить", "перезагрузить"}


@dataclass(frozen=True)
class Button:
    """Идентичность элемента, действительная только внутри указанного окна."""
    runtime_id: tuple[int, ...]
    name: str


def _elements(hwnd: int):
    # При сборке comtypes помещает типы Windows в EXE, из исходников создаёт при первом запросе.
    try:
        from comtypes.gen import UIAutomationClient as ui
    except ImportError:
        ui = comtypes.client.GetModule("UIAutomationCore.dll")
    client = comtypes.client.CreateObject(ui.CUIAutomation, interface=ui.IUIAutomation)
    root = client.ElementFromHandle(hwnd)
    condition = client.CreatePropertyCondition(ui.UIA_ControlTypePropertyId, ui.UIA_ButtonControlTypeId)
    items = root.FindAll(ui.TreeScope_Descendants, condition)
    count = 0
    for index in range(min(items.Length, 512)):
        item = items.GetElement(index)
        if item.CurrentIsOffscreen or not item.CurrentIsEnabled or item.CurrentIsPassword:
            continue
        name = " ".join((item.CurrentName or "").split())
        if not name or len(name) > 140 or name.casefold() in SYSTEM_CONTROLS:
            continue
        if not item.GetCurrentPropertyValue(ui.UIA_IsInvokePatternAvailablePropertyId):
            continue
        pattern = item.GetCurrentPattern(ui.UIA_InvokePatternId)
        if not pattern:
            continue
        yield Button(tuple(item.GetRuntimeId()), name), pattern.QueryInterface(ui.IUIAutomationInvokePattern)
        count += 1
        if count >= 25:
            break


def buttons(hwnd: int) -> tuple[Button, ...]:
    """Вернуть только доступные кнопки с именем и действием Invoke."""
    return tuple(button for button, _ in _elements(hwnd))


def invoke(target: tuple[int, int, str], button: Button) -> str:
    """Повторно проверить окно и точную кнопку перед одним нажатием."""
    if target not in desktop.windows() or speech.is_cancelled():
        return "Окно изменилось. Покажи кнопки ещё раз."
    for current, action in _elements(target[0]):
        if current == button and not speech.is_cancelled():
            action.Invoke()
            return f"Нажала «{button.name}»."
    return "Кнопка уже изменилась. Попроси показать кнопки снова."


def select_window(name: str, previous):
    """Использовать явно названное, активное стороннее или выбранное ранее окно."""
    live = desktop.windows()
    if name:
        return desktop.match_windows(live, name)
    active = win32gui.GetForegroundWindow()
    foreground = [w for w in live if w[0] == active]
    if foreground:
        return foreground
    return [previous] if previous in live else []


def select_from_list(session, choice: str) -> str:
    """Выбрать окно из показанного списка и сверить его идентичность с Windows."""
    candidates = session.window_choices
    if choice in NUMBERS:
        choice = str(NUMBERS.index(choice) + 1)
    if re.fullmatch(r"\d{1,2}", choice):
        index = int(choice) - 1
        matches = candidates[index:index + 1] if index >= 0 else ()
    else:
        matches = desktop.match_windows(candidates, choice)
    if len(matches) != 1:
        return session.ask("кнопки выбранного окна", "Какой номер в списке окон?")[1]
    if matches[0] not in desktop.windows():
        return "Это окно уже изменилось. Попроси показать кнопки ещё раз."
    session.window_choices = ()
    return describe_target(session, matches[0])


def describe(session, name: str) -> str:
    """Сохранить локальный снимок доступных кнопок и показать нумерованный список."""
    session.button_target = None
    session.buttons = ()
    windows = select_window(name, session.selected_window)
    if len(windows) != 1:
        if not windows and name:
            return f"Не вижу окна «{name}». Скажи «список окон»."
        options = tuple((windows or desktop.windows())[:8])
        if not options:
            return "Открытых окон не вижу. Открой приложение и попроси показать его кнопки."
        session.window_choices = options
        listing = "\n".join(f"{i}. {w[2]}" for i, w in enumerate(options, 1))
        speech.publish("assistant", listing)
        return session.ask("кнопки выбранного окна", "В каком окне посмотреть кнопки? Назови номер или заголовок.")[1]
    return describe_target(session, windows[0])


def describe_target(session, target: tuple[int, int, str]) -> str:
    """Прочитать кнопки выбранного окна без повторного поиска по похожим заголовкам."""
    try:
        available = buttons(target[0])
    except (OSError, comtypes.COMError):
        return "Windows не дала прочитать кнопки этого окна."
    if not available:
        return f"В окне «{target[2]}» Windows не показывает доступных кнопок."
    session.button_target = session.selected_window = target
    session.buttons = available
    listing = "\n".join(f"{i}. {b.name}" for i, b in enumerate(available, 1))
    speech.publish("assistant", listing)
    first = ", ".join(b.name for b in available[:3])
    return f"В окне «{target[2]}» доступны кнопки: {first}. Назови номер или название."


NUMBERS = "один два три четыре пять шесть семь восемь девять десять".split()


def choose(available: tuple[Button, ...], text: str) -> list[Button]:
    """Разобрать номер либо найти однозначное совпадение по названию."""
    key = safety.normalize_text(text).strip("«»\"")
    if key in NUMBERS:
        key = str(NUMBERS.index(key) + 1)
    if re.fullmatch(r"\d{1,2}", key):
        index = int(key) - 1
        return [available[index]] if 0 <= index < len(available) else []
    exact = [b for b in available if safety.normalize_text(b.name) == key]
    return exact or [b for b in available if key and key in safety.normalize_text(b.name)]
