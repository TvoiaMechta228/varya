"""Настройки ассистента и единственный белый список программ."""

import os
from pathlib import Path

ASSISTANT_NAME = "Варя"
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
LOG_FILE = BASE_DIR / "logs" / "varya.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3
PICTURES_DIR = Path.home() / "Pictures"
WINDOWS_DIR = Path(os.environ.get("SystemRoot", r"C:\Windows"))
SHUTDOWN_EXE = str(WINDOWS_DIR / "System32" / "shutdown.exe")
BROWSER_URL = "https://ya.ru"
# Windows 11 может предоставлять Paint через App Execution Alias.
PAINT_EXE = WINDOWS_DIR / "System32" / "mspaint.exe"
if not PAINT_EXE.is_file():
    PAINT_EXE = Path(os.environ.get(
        "LOCALAPPDATA", str(Path.home() / "AppData" / "Local"),
    )) / "Microsoft" / "WindowsApps" / "mspaint.exe"

# Кортеж — только фиксированные аргументы; None означает браузер.
APP_WHITELIST: dict[str, tuple[str, ...] | None] = {
    "блокнот": (str(WINDOWS_DIR / "System32" / "notepad.exe"),),
    "калькулятор": (str(WINDOWS_DIR / "System32" / "calc.exe"),),
    "проводник": (str(WINDOWS_DIR / "explorer.exe"),),
    "paint": (str(PAINT_EXE),),
    "браузер": None,
}
