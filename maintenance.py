"""Обслуживание Windows через корзину оболочки и каталог WinGet."""

import os
import re
import subprocess
import time
from pathlib import Path

import pythoncom
import pywintypes
from win32com.shell import shell, shellcon

from workspace_tasks import ordinary, signature

PACKAGES = {
    "7-zip": "7zip.7zip",
    "vlc": "VideoLAN.VLC",
    "powertoys": "Microsoft.PowerToys",
}
RECYCLE_ON_DELETE = 0x00080000


def temp_root() -> Path:
    return Path(shell.SHGetFolderPath(0, shellcon.CSIDL_LOCAL_APPDATA, 0, 0)) / "Temp"


def temp_candidates() -> tuple:
    root = temp_root()
    if not ordinary(root, directory=True):
        return ()
    cutoff = time.time() - 7 * 86400
    items = []
    for path in root.glob("*.tmp"):
        try:
            if ordinary(path) and path.stat().st_mtime < cutoff:
                items.append((path.name, signature(path)))
        except OSError:
            continue
        if len(items) >= 50:
            break
    return tuple(items)


def recycle(items: tuple, stopped) -> str:
    root = temp_root()
    done = 0
    for name, stamp in items:
        if stopped() or not ordinary(root, directory=True):
            break
        if name != Path(name).name or Path(name).suffix.lower() != ".tmp":
            continue
        path = root / name
        try:
            if not ordinary(path) or signature(path) != stamp:
                continue
            op = pythoncom.CoCreateInstance(
                shell.CLSID_FileOperation,
                None,
                pythoncom.CLSCTX_ALL,
                shell.IID_IFileOperation,
            )
            op.SetOperationFlags(
                RECYCLE_ON_DELETE
                | shellcon.FOF_NOERRORUI
                | shellcon.FOF_NOCONFIRMATION
                | shellcon.FOF_SILENT
            )
            item = shell.SHCreateItemFromParsingName(
                str(path), None, shell.IID_IShellItem
            )
            op.DeleteItem(item, None)
            op.PerformOperations()
            if not op.GetAnyOperationsAborted() and not path.exists():
                done += 1
        except (OSError, pywintypes.com_error):
            continue
    return f"В корзину перемещено файлов — {done} из {len(items)}. Восстановить их можно из корзины Windows."


def winget(action: str, package: str, stopped) -> tuple[bool, str]:
    exe = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WindowsApps" / "winget.exe"
    if not exe.is_file():
        return False, "WinGet не найден. Он входит в «Установщик приложений» Windows."
    if action == "list":
        args = ["upgrade"]
    elif action in {"upgrade", "install"} and package in PACKAGES:
        args = [action, "--id", PACKAGES[package], "--exact", "--scope", "user"]
    else:
        raise ValueError("Неизвестный пакет или операция.")
    if stopped():
        raise InterruptedError
    args += ["--source", "winget", "--disable-interactivity"]
    with subprocess.Popen(
        [str(exe), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ) as proc:
        deadline = time.monotonic() + (35 if action == "list" else 180)
        while True:
            try:
                output = proc.communicate(timeout=0.4)[0]
                break
            except subprocess.TimeoutExpired:
                if stopped() or time.monotonic() > deadline:
                    proc.kill()
                    proc.communicate()
                    return (
                        False,
                        "Ожидание WinGet остановлено. Проверь состояние программы в Windows.",
                    )
    output = output.decode("utf-8-sig", errors="replace")
    output = re.sub(r"\x1b\[[\d;?]*[a-zA-Z]", "", output).strip()
    return proc.returncode == 0, output[
        -16000:
    ] or f"Код завершения WinGet — {proc.returncode}."
