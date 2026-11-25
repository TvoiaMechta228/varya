"""Напоминания через планировщик Windows при закрытом окне приложения."""

from datetime import datetime
import hashlib
import logging
import os
from pathlib import Path

import settings


def missing_task(error: Exception) -> bool:
    """Учитывать внутренний HRESULT, обёрнутый в DISP_E_EXCEPTION COM."""
    code = getattr(error, "hresult", 0)
    details = getattr(error, "excepinfo", None)
    if details and len(details) > 5:
        code = details[5]
    return code in {-2147024894, -2147216625}


def refresh(deadlines: list[float]) -> str | None:
    """Зарегистрировать ближайший срок для текущего пользователя без пароля и администратора."""
    if not settings.snapshot().get("scheduled_reminders"):
        deadlines = []
    root = Path(os.environ.get("VARYA_HOME", Path(__file__).parent))
    executable = root / "Varya.exe"
    if not executable.is_file():
        return (
            None
            if not deadlines
            else "Запуск напоминаний при закрытом окне доступен в установленной сборке Вари."
        )
    import pythoncom
    from win32com.client import Dispatch

    identity = hashlib.sha256(str(settings.DATA_DIR).casefold().encode()).hexdigest()[
        :16
    ]
    name = "Varya-Reminder-" + identity
    pythoncom.CoInitialize()
    service = folder = task = trigger = action = None
    try:
        service = Dispatch("Schedule.Service")
        service.Connect()
        folder = service.GetFolder("\\")
        if not deadlines:
            try:
                folder.DeleteTask(name, 0)
            except Exception as error:
                if not missing_task(error):
                    return "Не удалось убрать системное напоминание. Проверь планировщик Windows."
            return None
        task = service.NewTask(0)
        task.RegistrationInfo.Description = (
            "Запустить Варю для сохранённого напоминания текущего пользователя."
        )
        task.Principal.LogonType = 3
        task.Principal.RunLevel = 0
        task.Settings.Enabled = True
        task.Settings.StartWhenAvailable = True
        task.Settings.WakeToRun = True
        task.Settings.DisallowStartIfOnBatteries = False
        task.Settings.StopIfGoingOnBatteries = False
        task.Settings.ExecutionTimeLimit = "PT0S"
        task.Settings.MultipleInstances = 2
        trigger = task.Triggers.Create(1)
        trigger.StartBoundary = (
            datetime.fromtimestamp(min(deadlines))
            .astimezone()
            .isoformat(timespec="seconds")
        )
        trigger.Enabled = True
        action = task.Actions.Create(0)
        action.Path = str(executable)
        action.WorkingDirectory = str(root)
        # Пустые credentials используют текущий интерактивный токен Connect().
        folder.RegisterTaskDefinition(name, task, 6, None, None, 3)
        return None
    except Exception:
        logging.getLogger(__name__).exception(
            "Регистрация системного напоминания не выполнена"
        )
        return "Планировщик Windows не разрешил системное напоминание. Таймер работает, пока Варя открыта."
    finally:
        action = trigger = task = folder = service = None
        pythoncom.CoUninitialize()
