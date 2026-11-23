"""Значок области уведомлений и глобальная клавиша возврата к Варе."""

import os
import threading

import win32api
import win32con
import win32gui


class Tray:
    def __init__(self, events):
        self.events = events
        self.hwnd = 0
        self.ready = False
        self.hotkey = False
        self.stopped = threading.Event()
        threading.Thread(target=self.loop, name="VaryaTray", daemon=True).start()

    def loop(self):
        callback = win32con.WM_APP + 20
        taskbar = win32gui.RegisterWindowMessage("TaskbarCreated")
        klass = f"VaryaTray{os.getpid()}"
        instance = win32api.GetModuleHandle(None)
        icon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)

        def add_icon(hwnd):
            win32gui.Shell_NotifyIcon(
                win32gui.NIM_ADD,
                (
                    hwnd,
                    1,
                    win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
                    callback,
                    icon,
                    "Варя · J.A.R.V.I.S.",
                ),
            )
            self.ready = True

        def proc(hwnd, msg, wparam, lparam):
            if msg == taskbar:
                add_icon(hwnd)
            elif msg == win32con.WM_HOTKEY or (
                msg == callback and lparam == win32con.WM_LBUTTONUP
            ):
                self.events.put(("restore", ""))
            elif msg == callback and lparam == win32con.WM_RBUTTONUP:
                menu = win32gui.CreatePopupMenu()
                try:
                    win32gui.AppendMenu(menu, win32con.MF_STRING, 1, "Открыть Варю")
                    win32gui.AppendMenu(menu, win32con.MF_STRING, 2, "Завершить работу")
                    win32gui.SetForegroundWindow(hwnd)
                    x, y = win32gui.GetCursorPos()
                    item = win32gui.TrackPopupMenu(
                        menu, win32con.TPM_RETURNCMD, x, y, 0, hwnd, None
                    )
                    if item:
                        self.events.put(("restore" if item == 1 else "exit", ""))
                finally:
                    win32gui.DestroyMenu(menu)
            elif msg == win32con.WM_DESTROY:
                if self.hotkey:
                    win32gui.UnregisterHotKey(hwnd, 1)
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, 1))
                self.ready = False
                win32gui.PostQuitMessage(0)
            else:
                return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)
            return 0

        wc = win32gui.WNDCLASS()
        wc.lpszClassName, wc.hInstance, wc.lpfnWndProc = klass, instance, proc
        try:
            win32gui.RegisterClass(wc)
            self.hwnd = win32gui.CreateWindow(
                klass, "", 0, 0, 0, 0, 0, 0, 0, instance, None
            )
            add_icon(self.hwnd)
            try:
                win32gui.RegisterHotKey(
                    self.hwnd,
                    1,
                    win32con.MOD_CONTROL | win32con.MOD_ALT | 0x4000,
                    ord("J"),
                )
                self.hotkey = True
            except win32gui.error:
                self.events.put(
                    (
                        "tray_status",
                        "Ctrl+Alt+J занято. Вернуться можно через значок Вари.",
                    )
                )
            if self.stopped.is_set():
                win32gui.DestroyWindow(self.hwnd)
            else:
                win32gui.PumpMessages()
        except win32gui.error:
            self.events.put(("tray_status", "Область уведомлений Windows недоступна."))
        finally:
            self.ready = False
            self.hwnd = 0
            try:
                win32gui.UnregisterClass(klass, instance)
            except win32gui.error:
                pass

    def close(self):
        self.stopped.set()
        if self.hwnd and win32gui.IsWindow(self.hwnd):
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
