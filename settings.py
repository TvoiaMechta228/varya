"""Проверяемые пользовательские настройки с атомарным сохранением."""

import json
import os
from pathlib import Path
import threading

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Varya"
SETTINGS_FILE = DATA_DIR / "settings.json"
DEFAULTS = {
    "voice": "neural", "rate": 175, "volume": 85, "wake_word": False,
}
_lock = threading.RLock()
_current = dict(DEFAULTS)


def validate(values: dict) -> dict:
    """Принять только известные настройки в допустимых пределах."""
    result = dict(DEFAULTS)
    if values.get("voice") in {"neural", "sapi"}:
        result["voice"] = values["voice"]
    for key, low, high in (("rate", 120, 230), ("volume", 10, 100)):
        if type(values.get(key)) is int:
            result[key] = max(low, min(high, values[key]))
    if type(values.get("wake_word")) is bool:
        result["wake_word"] = values["wake_word"]
    if isinstance(values.get("sapi_voice"), str) and values["sapi_voice"]:
        result["sapi_voice"] = values["sapi_voice"]
    if values.get("installed_apps") is True:
        result["installed_apps"] = True
    if values.get("mode") in {"Наблюдатель", "Автономный", "Расширенный", "Помощник"}:
        result["mode"] = values["mode"]
    apps = values.get("work_apps")
    if isinstance(apps, list):
        from config import APP_WHITELIST
        result["work_apps"] = [name for name in APP_WHITELIST if name in apps]
    project = values.get("project")
    if isinstance(project, str) and project and Path(project).is_absolute():
        result["project"] = project
    return result


def load() -> None:
    """Загрузить настройки; при повреждении использовать значения по умолчанию."""
    global _current
    with _lock:
        try:
            values = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            _current = validate(values if isinstance(values, dict) else {})
        except (OSError, ValueError):
            _current = dict(DEFAULTS)


def snapshot() -> dict:
    """Вернуть независимую копию настроек для рабочего потока."""
    with _lock:
        return dict(_current)


def save(values: dict) -> None:
    """Сохранить только проверенные поля и обновить активные настройки."""
    global _current
    checked = validate(values)
    with _lock:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = SETTINGS_FILE.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(checked, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(SETTINGS_FILE)
        _current = checked
