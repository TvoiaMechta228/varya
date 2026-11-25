"""Планировщик использует интерактивную учётную запись и сохраняет возможность отмены."""

from unittest.mock import MagicMock

import reminder_schedule
import settings


def test_nearest_reminder_has_no_elevation_or_password(tmp_path, monkeypatch):
    import win32com.client

    (tmp_path / "Varya.exe").touch()
    monkeypatch.setenv("VARYA_HOME", str(tmp_path))
    monkeypatch.setitem(settings._current, "scheduled_reminders", True)
    service = MagicMock()
    monkeypatch.setattr(win32com.client, "Dispatch", lambda _: service)
    assert reminder_schedule.refresh([2000000100, 2000000000]) is None
    task = service.NewTask.return_value
    assert task.Principal.LogonType == 3
    assert task.Principal.RunLevel == 0
    assert task.Settings.WakeToRun is True
    assert task.Settings.ExecutionTimeLimit == "PT0S"
    assert task.Actions.Create.return_value.Path == str(tmp_path / "Varya.exe")
    args = service.GetFolder.return_value.RegisterTaskDefinition.call_args.args
    assert args[-3:] == (None, None, 3)


def test_nested_com_not_found_is_not_reported_as_failure():
    class Missing(Exception):
        hresult = -2147352567
        excepinfo = (0, None, None, None, 0, -2147024894)

    assert reminder_schedule.missing_task(Missing())


def test_disable_removes_registered_reminder(tmp_path, monkeypatch):
    import win32com.client

    (tmp_path / "Varya.exe").touch()
    monkeypatch.setenv("VARYA_HOME", str(tmp_path))
    service = MagicMock()
    monkeypatch.setattr(win32com.client, "Dispatch", lambda _: service)
    assert reminder_schedule.refresh([2000000000]) is None
    service.GetFolder.return_value.DeleteTask.assert_called_once()
    service.NewTask.assert_not_called()
