"""Управление рабочим столом по прямым командам пользователя."""

import ctypes
import os
from pathlib import Path
import re

import win32con
import win32gui
import win32process
from win32com.shell import shell, shellcon

import safety
import speech


def windows() -> list[tuple[int, int, str]]:
    """Получить видимые верхнеуровневые окна с заголовком."""
    items = []

    def visit(hwnd, _):
        title = win32gui.GetWindowText(hwnd).strip()
        if title and win32gui.IsWindowVisible(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid != os.getpid():
                items.append((hwnd, pid, title))
        return True

    win32gui.EnumWindows(visit, None)
    return items


WINDOW_VERBS = {"сверни", "разверни", "закрой", "переключись на"}


def match_windows(items, name: str) -> list[tuple[int, int, str]]:
    """Предпочесть точное совпадение заголовка частичному."""
    key = safety.normalize_text(name)
    exact = [w for w in items if key == safety.normalize_text(w[2])]
    return exact or [w for w in items if key in safety.normalize_text(w[2])]


def named_window(verb: str, name: str) -> str:
    """Выбрать окно по однозначному названию."""
    matches = match_windows(windows(), name)
    if not matches:
        return "Не вижу окна с таким названием."
    if len(matches) > 1:
        return "Подходит несколько окон. Уточни название. " + ". ".join(
            w[2][:100] for w in matches[:3])
    return apply_window(verb, matches[0])


def apply_window(verb: str, target: tuple[int, int, str]) -> str:
    """Выполнить действие с проверенной целью."""
    hwnd, _, title = target
    if verb == "закрой":
        if not safety.confirm("close_window", target=title):
            return "Окно оставлено открытым."
        if speech.is_cancelled() or target not in windows():
            return "Окно изменилось. Закрытие отменено."
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return "Отправила запрос на закрытие окна."
    if verb == "сверни":
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    elif verb == "разверни":
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    else:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    return "Готово."


def documents() -> Path:
    """Использовать реальную папку документов, в том числе перенаправленную."""
    return Path(shell.SHGetFolderPath(0, shellcon.CSIDL_PERSONAL, 0, 0))


def create_folder(name: str) -> str:
    """Создать один новый каталог в Документах после подтверждения."""
    if (not re.fullmatch(r"[\wА-Яа-яЁё -]{1,64}", name)
            or name != name.strip() or Path(name).is_reserved()):
        return "Для имени папки подойдут буквы, цифры, пробел и дефис."
    path = documents() / name
    if path.exists():
        return "Такая папка или файл уже есть."
    if not safety.confirm("create_folder", target=f"Документы, {name}"):
        return "Создание папки отменено."
    if speech.is_cancelled():
        return "Отменено."
    path.mkdir()
    return f"Создала папку {name} в документах."


def dictate(text: str) -> str:
    """Вставить одну строку в редактор активного Блокнота с поддержкой отмены."""
    if not text.strip() or len(text) > 2000 or not text.isprintable():
        return "Продиктуй одну строку длиной до двух тысяч символов."
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd or win32gui.GetClassName(hwnd).casefold() != "notepad":
        return "Сначала переключись на документ в Блокноте."
    editors: list[int] = []

    def visit(child: int, _: object) -> bool:
        if (win32gui.IsWindowVisible(child) and win32gui.IsWindowEnabled(child)
                and win32gui.GetClassName(child).casefold() in {"edit", "richeditd2dpt"}):
            editors.append(child)
        return True

    win32gui.EnumChildWindows(hwnd, visit, None)
    if len(editors) != 1 or hwnd != win32gui.GetForegroundWindow() or speech.is_cancelled():
        return "Не удалось однозначно выбрать текстовое поле Блокнота."
    # EM_REPLACESEL вставляет Unicode в конкретное поле и сохраняет историю отмены.
    buffer = ctypes.create_unicode_buffer(text)
    win32gui.SendMessageTimeout(editors[0], win32con.EM_REPLACESEL, 1,
                                ctypes.addressof(buffer), win32con.SMTO_ABORTIFHUNG, 1500)
    return "Текст введён."


def execute(command: str, raw: str) -> str | None:
    """Обработать одну команду. None означает, что фраза не подошла."""
    phrase = re.fullmatch(r"напечатай\s+(.+)", raw.strip(), re.I)
    if phrase:
        return dictate(phrase[1])
    if command in {"какие окна открыты", "список окон"}:
        titles = [w[2] for w in windows()]
        return ". ".join(titles[:12]) or "Открытых окон не вижу."
    match = re.fullmatch(r"(переключись на|сверни|разверни|закрой) окно (.+)", command)
    if match:
        return named_window(*match.groups())
    match = re.fullmatch(r"создай папку\s+(.+)", raw.strip(), re.I)
    if match:
        return create_folder(match[1])
    folders = {"открой документы": shellcon.CSIDL_PERSONAL,
               "открой изображения": shellcon.CSIDL_MYPICTURES,
               "открой папку рабочего стола": shellcon.CSIDL_DESKTOPDIRECTORY}
    if command in folders:
        os.startfile(shell.SHGetFolderPath(0, folders[command], 0, 0))
        return "Открываю папку."
    if not (command.startswith("курсор на ") or command in {
        "где курсор", "левый клик", "правый клик", "двойной клик",
        "прокрути вниз", "прокрути вверх",
    }):
        return None
    import pyautogui as mouse

    if command == "где курсор":
        x, y = mouse.position()
        return f"Курсор на {x}, {y}."
    match = re.fullmatch(r"курсор на (\d{1,5}) (\d{1,5})", command)
    if match:
        x, y = map(int, match.groups())
        if not mouse.onScreen(x, y):
            return "Эта точка за границей основного экрана."
        mouse.moveTo(x, y, duration=0.18)
        return f"Курсор на {x}, {y}."
    if command in {"левый клик", "правый клик", "двойной клик"}:
        mouse.click(button="right" if command == "правый клик" else "left",
                    clicks=2 if command == "двойной клик" else 1, interval=0.1)
        return "Готово."
    if command in {"прокрути вниз", "прокрути вверх"}:
        mouse.scroll(-4 if command.endswith("вниз") else 4)
        return "Готово."
    return None
