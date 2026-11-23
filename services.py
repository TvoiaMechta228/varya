"""Встроенные адаптеры к открытому профилю Outlook и локальной папке OneDrive."""

import os
from datetime import datetime, timedelta
from pathlib import Path

import pywintypes
import win32com.client


def cloud_folder() -> Path | None:
    for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(key)
        if value and Path(value).is_absolute() and Path(value).is_dir():
            return Path(value)
    return None


def agenda() -> str:
    """Прочитать сегодняшние встречи уже открытого классического Outlook."""
    try:
        outlook = win32com.client.GetActiveObject("Outlook.Application")
        calendar = outlook.GetNamespace("MAPI").GetDefaultFolder(9).Items
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        calendar.Sort("[Start]")
        calendar.IncludeRecurrences = True
        # Jet-фильтр Outlook принимает время в региональном формате Windows.
        import win32api

        def local_date(dt):
            return (
                win32api.GetDateFormat(0x400, 1, dt)
                + " "
                + win32api.GetTimeFormat(0x400, 2, dt)
            )

        today = calendar.Restrict(
            f"[Start] < '{local_date(end)}' AND [End] > '{local_date(start)}'"
        )
        appointments = []
        for item in today:
            appointments.append(
                f"{item.Start:%H:%M}  {str(item.Subject or 'Встреча')[:120]}"
            )
            if len(appointments) == 30:
                break
        return "\n".join(appointments) or "В календаре на сегодня встреч нет."
    except (pywintypes.com_error, pywintypes.error, AttributeError, OSError):
        return "Календарь недоступен. Открой классический Outlook с настроенной учётной записью."


def inbox() -> str:
    """Показать до пяти тем непрочитанных писем, сохраняя их статус."""
    try:
        outlook = win32com.client.GetActiveObject("Outlook.Application")
        unread = (
            outlook.GetNamespace("MAPI")
            .GetDefaultFolder(6)
            .Items.Restrict("[UnRead] = True")
        )
        unread.Sort("[ReceivedTime]", True)
        lines = []
        for msg in unread:
            lines.append(str(msg.Subject or "Без темы")[:160])
            if len(lines) == 5:
                break
        return "\n".join(lines) or "Непрочитанных писем нет."
    except (pywintypes.com_error, AttributeError, OSError):
        return "Открой классический Outlook с настроенной почтой и повтори просьбу."
