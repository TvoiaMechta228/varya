"""Настройки ассистента и единственный белый список программ."""

import os
import sys
from pathlib import Path

ASSISTANT_NAME = "Варя"
ASSISTANT_VERSION = "2.2.0"
LANGUAGE = "ru-RU"
LISTEN_TIMEOUT = 5.0
PHRASE_TIME_LIMIT = 8.0
CONFIRM_TIMEOUT = 5.0
CONFIRM_PHRASE_TIME_LIMIT = 4.0
NETWORK_TIMEOUT = 10.0
AMBIENT_DURATION = 0.5
ERROR_RETRY_DELAY = 2.0
MICROPHONE_INDEX: int | None = None
SPEECH_RATE = 175
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Varya"
    if getattr(sys, "frozen", False)
    else BASE_DIR
)
LOG_FILE = DATA_DIR / "logs" / "varya.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3
PICTURES_DIR = Path.home() / "Pictures"
WINDOWS_DIR = Path(os.environ.get("SystemRoot", r"C:\Windows"))
SHUTDOWN_EXE = str(WINDOWS_DIR / "System32" / "shutdown.exe")
BROWSER_URL = "https://ya.ru"
# Адреса и псевдонимы сервисов задаются разработчиком, а не моделью.
# Значения: название, домашняя страница, страница поиска, параметр поиска, имена.
YANDEX_SERVICES = {
    "search": (
        "Яндекс",
        BROWSER_URL,
        "https://yandex.ru/search/",
        "text",
        ("яндекс", "поиск яндекса"),
    ),
    "music": (
        "Яндекс Музыка",
        "https://music.yandex.ru/",
        "https://music.yandex.ru/search",
        "text",
        (
            "яндекс музыка",
            "яндекс музыку",
            "яндекс музыке",
            "музыка яндекса",
            "музыку яндекса",
            "музыке яндекса",
            "яндекс.музыка",
            "яндекс.музыку",
            "яндекс.музыке",
            "yandex music",
        ),
    ),
    "maps": (
        "Яндекс Карты",
        "https://yandex.ru/maps/",
        "https://yandex.ru/maps/",
        "text",
        (
            "яндекс карты",
            "яндекс картах",
            "карты яндекса",
            "картах яндекса",
            "яндекс.карты",
        ),
    ),
    "market": (
        "Яндекс Маркет",
        "https://market.yandex.ru/",
        "https://market.yandex.ru/search",
        "text",
        (
            "яндекс маркет",
            "яндекс маркете",
            "маркете яндекса",
            "маркет яндекса",
            "яндекс.маркет",
        ),
    ),
    "mail": (
        "Яндекс Почта",
        "https://mail.yandex.ru/",
        "",
        "",
        (
            "яндекс почта",
            "яндекс почту",
            "яндекс почте",
            "почта яндекса",
            "почту яндекса",
            "яндекс.почта",
        ),
    ),
    "disk": (
        "Яндекс Диск",
        "https://disk.yandex.ru/client/disk",
        "",
        "",
        ("яндекс диск", "яндекс диске", "диск яндекса", "яндекс.диск"),
    ),
    "calendar": (
        "Яндекс Календарь",
        "https://calendar.yandex.ru/",
        "",
        "",
        ("яндекс календарь", "календарь яндекса", "яндекс.календарь"),
    ),
    "translate": (
        "Яндекс Переводчик",
        "https://translate.yandex.ru/",
        "https://translate.yandex.ru/",
        "text",
        ("яндекс переводчик", "переводчик яндекса", "яндекс.переводчик"),
    ),
    "weather": (
        "Яндекс Погода",
        "https://yandex.ru/pogoda/",
        "",
        "",
        ("яндекс погода", "яндекс погоду", "погода яндекса", "яндекс.погода"),
    ),
    "images": (
        "Яндекс Картинки",
        "https://yandex.ru/images/",
        "https://yandex.ru/images/search/",
        "text",
        (
            "яндекс картинки",
            "яндекс картинках",
            "картинки яндекса",
            "картинках яндекса",
            "яндекс.картинки",
        ),
    ),
    "video": (
        "Яндекс Видео",
        "https://yandex.ru/video/",
        "https://yandex.ru/video/search/",
        "text",
        ("яндекс видео", "видео яндекса", "яндекс.видео"),
    ),
    "kinopoisk": (
        "Кинопоиск",
        "https://www.kinopoisk.ru/",
        "https://www.kinopoisk.ru/index.php",
        "kp_query",
        ("кинопоиск", "кинопоиске", "яндекс кинопоиск"),
    ),
    "books": (
        "Яндекс Книги",
        "https://books.yandex.ru/",
        "",
        "",
        ("яндекс книги", "яндекс книгах", "книги яндекса", "яндекс.книги"),
    ),
    "travel": (
        "Яндекс Путешествия",
        "https://travel.yandex.ru/",
        "",
        "",
        ("яндекс путешествия", "путешествия яндекса"),
    ),
    "telemost": (
        "Яндекс Телемост",
        "https://telemost.yandex.ru/",
        "",
        "",
        ("яндекс телемост", "телемост яндекса"),
    ),
    "all": (
        "Все сервисы Яндекса",
        "https://yandex.ru/all",
        "",
        "",
        ("все сервисы яндекса", "сервисы яндекса"),
    ),
}
WEB_SHORTCUTS = {
    "яндекс музыка": "https://music.yandex.ru/",
    "яндекс музыку": "https://music.yandex.ru/",
    "музыка": "https://music.yandex.ru/",
    "музыку": "https://music.yandex.ru/",
    "ютуб": "https://www.youtube.com/",
    "youtube": "https://www.youtube.com/",
    "яндекс": BROWSER_URL,
}
APP_ALIASES = {
    "интернет": "браузер",
    "браузера": "браузер",
    "яндекс браузер": "браузер",
    "яндекс-браузер": "браузер",
    "нотпад": "блокнот",
    "пэйнт": "paint",
    "паинт": "paint",
}
# Windows 11 может предоставлять Paint через App Execution Alias.
PAINT_EXE = WINDOWS_DIR / "System32" / "mspaint.exe"
if not PAINT_EXE.is_file():
    PAINT_EXE = (
        Path(
            os.environ.get(
                "LOCALAPPDATA",
                str(Path.home() / "AppData" / "Local"),
            )
        )
        / "Microsoft"
        / "WindowsApps"
        / "mspaint.exe"
    )

# Кортеж — только фиксированные аргументы; None означает браузер.
APP_WHITELIST: dict[str, tuple[str, ...] | None] = {
    "блокнот": (str(WINDOWS_DIR / "System32" / "notepad.exe"),),
    "калькулятор": (str(WINDOWS_DIR / "System32" / "calc.exe"),),
    "проводник": (str(WINDOWS_DIR / "explorer.exe"),),
    "paint": (str(PAINT_EXE),),
    "браузер": None,
}
