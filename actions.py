"""Разрешённые действия с ПК; пользовательский текст не запускается как код."""

from datetime import datetime
import logging
import os
from pathlib import Path
import subprocess
from urllib.parse import urlencode
import webbrowser

import config
import safety

logger = logging.getLogger(__name__)
MEDIA_KEYS = {
    "громче": "volumeup", "тише": "volumedown",
    "переключи звук": "volumemute", "пауза музыки": "playpause",
}
WINDOW_KEYS = {
    "сверни окно": ("win", "down"),
    "разверни окно": ("win", "up"),
    "покажи рабочий стол": ("win", "d"),
}


def media_control(command: str) -> None:
    """Отправить только разрешённую мультимедийную клавишу."""
    import pyautogui

    key = MEDIA_KEYS[command]
    pyautogui.press(key, presses=3 if command in {"громче", "тише"} else 1)


def window_control(command: str) -> None:
    """Выполнить разрешённое сочетание для активного окна."""
    import pyautogui

    pyautogui.hotkey(*WINDOW_KEYS[command])


def _require_windows() -> None:
    """Ограничить системные действия платформой Windows."""
    if os.name != "nt":
        raise OSError("Системное действие доступно только в Windows")


def _open_url(url: str) -> None:
    """Открыть URL и сообщить об отказе браузера."""
    if os.environ.get("VARYA_NATIVE") == "1":
        import browser_tools
        browser_tools.open_url(url)
        return
    if not webbrowser.open(url, new=2):
        raise OSError("Не удалось открыть браузер")


def open_app(name: str) -> bool:
    """Запустить только точное имя из белого списка; иначе вернуть False."""
    name = " ".join(name.lower().split())
    if name not in config.APP_WHITELIST:
        logger.warning("Программа вне белого списка: %s", name)
        return False
    command = config.APP_WHITELIST[name]
    if command is None:
        _open_url(config.BROWSER_URL)
    else:
        _require_windows()
        subprocess.Popen(command, shell=False)
    return True


def open_service(name: str) -> None:
    """Открыть известный веб-сервис по фиксированному адресу из конфигурации."""
    _open_url(config.WEB_SHORTCUTS[name])


def open_installed(name: str) -> bool:
    """Открыть единственное точное совпадение в каталоге Windows."""
    from win32com.client import Dispatch

    catalog = Dispatch("Shell.Application").NameSpace("shell:AppsFolder")
    if catalog is None:
        return False
    found = [item for item in catalog.Items() if safety.normalize_text(item.Name) == name]
    if len(found) != 1:
        return False
    found[0].InvokeVerb("open")
    return True


def search_web(query: str) -> None:
    """Открыть поиск Яндекса, кодируя запрос как параметр URL."""
    query = query.strip()
    if not query:
        raise ValueError("Поисковый запрос пуст")
    _open_url("https://yandex.ru/search/?" + urlencode({"text": query}))


def take_screenshot() -> Path:
    """Сохранить PNG в Pictures, не перезаписывая существующий файл."""
    import pyautogui

    config.PICTURES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot = pyautogui.screenshot()
    index = 0
    while True:
        suffix = f"_{index}" if index else ""
        path = config.PICTURES_DIR / f"screen_{stamp}{suffix}.png"
        try:
            stream = path.open("xb")
        except FileExistsError:
            index += 1
            continue
        try:
            with stream:
                screenshot.save(stream, format="PNG")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        logger.info("Скриншот сохранён: %s", path)
        return path


def shutdown_pc() -> bool:
    """Запланировать выключение после нового голосового подтверждения."""
    _require_windows()
    if not safety.confirm("shutdown") or safety.speech.is_cancelled():
        return False
    subprocess.run(
        [config.SHUTDOWN_EXE, "/s", "/t", "10"], check=True, shell=False,
    )
    return True


def reboot_pc() -> bool:
    """Запланировать перезагрузку только после голосового подтверждения."""
    _require_windows()
    if not safety.confirm("reboot") or safety.speech.is_cancelled():
        return False
    subprocess.run(
        [config.SHUTDOWN_EXE, "/r", "/t", "10"], check=True, shell=False,
    )
    return True


def cancel_shutdown() -> None:
    """Отменить запланированное выключение или перезагрузку Windows."""
    _require_windows()
    subprocess.run(
        [config.SHUTDOWN_EXE, "/a"], check=True, shell=False,
    )
