"""Измерения Windows и диагностика по короткой серии наблюдений."""

import os
import time
import winreg
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import psutil
import pywintypes
import win32pdh


def disk_root() -> str:
    return Path(os.environ.get("SystemRoot", r"C:\Windows")).anchor


class Monitor:
    """Считать загрузку процессора, GPU и скорость сети между двумя замерами."""

    def __init__(self) -> None:
        self.query = None
        self.counter = None
        self.gpu = None
        self.next_gpu = 0.0
        self.process = psutil.Process()
        self.net = psutil.net_io_counters()
        self.at = time.monotonic()
        psutil.cpu_percent()
        try:
            self.query = win32pdh.OpenQuery()
            self.counter = win32pdh.AddEnglishCounter(
                self.query, r"\GPU Engine(*)\Utilization Percentage"
            )
            win32pdh.CollectQueryData(self.query)
        except pywintypes.error:
            self.close()

    def close(self) -> None:
        if self.query is not None:
            win32pdh.CloseQuery(self.query)
            self.query = self.counter = None

    def sample(self) -> dict:
        gpu = self.gpu
        if self.counter is not None and time.monotonic() >= self.next_gpu:
            try:
                win32pdh.CollectQueryData(self.query)
                engines = defaultdict(float)
                for name, value in win32pdh.GetFormattedCounterArray(
                    self.counter, win32pdh.PDH_FMT_DOUBLE
                ).items():
                    if "_luid_" in name:
                        engines[name.partition("_luid_")[2]] += value
                if engines:
                    gpu = min(100, max(engines.values()))
                    self.gpu = gpu
            except pywintypes.error:
                pass
            self.next_gpu = time.monotonic() + 5
        now, net = time.monotonic(), psutil.net_io_counters()
        elapsed = max(0.01, now - self.at)
        received = max(0, net.bytes_recv - self.net.bytes_recv) / elapsed / 1024
        sent = max(0, net.bytes_sent - self.net.bytes_sent) / elapsed / 1024
        self.net, self.at = net, now
        memory, disk = psutil.virtual_memory(), psutil.disk_usage(disk_root())
        return dict(
            cpu=psutil.cpu_percent(),
            gpu=gpu,
            ram=memory.percent,
            disk=disk.percent,
            free=disk.free / 2**30,
            total=memory.total / 2**30,
            app_memory=self.process.memory_info().rss / 2**20,
            rx=received,
            tx=sent,
        )


def diagnose(cancelled, progress) -> tuple[str, str]:
    """Замерить нагрузку, автозапуск и процессы. Вернуть сводку и полный отчёт."""
    progress("Измеряю нагрузку")
    monitor = Monitor()
    rows = []
    try:
        for _ in range(4):
            if cancelled():
                raise InterruptedError
            time.sleep(0.25)
            rows.append(monitor.sample())
    finally:
        monitor.close()
    last = rows[-1]
    cpu = sum(row["cpu"] for row in rows) / len(rows)
    progress("Проверяю автозапуск и память процессов")
    apps = []
    for process in psutil.process_iter(["name", "memory_info"]):
        info = process.info
        if info["name"] and info["memory_info"]:
            apps.append((info["memory_info"].rss / 2**20, info["name"]))
    startup = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(
                hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
            ) as key:
                startup.extend(
                    winreg.EnumValue(key, i)[0]
                    for i in range(winreg.QueryInfoKey(key)[1])
                )
        except OSError:
            continue
    observations = []
    if cpu >= 80:
        observations.append("Во время замера была высокая нагрузка на процессор.")
    if last["ram"] >= 85:
        observations.append(
            "Мало свободной памяти. Проверь самые крупные процессы в отчёте."
        )
    if last["disk"] >= 90:
        observations.append("Системный диск почти заполнен.")
    conclusion = (
        " ".join(observations)
        or "За время замера перегрузки процессора, памяти или диска не обнаружила."
    )
    summary = f"Процессор {cpu:.0f}%, память {last['ram']:.0f}%, на диске свободно {last['free']:.1f} ГБ. {conclusion}"
    gpu = "нет показаний" if last["gpu"] is None else f"{last['gpu']:.0f}%"
    report = "\n".join(
        [
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            summary,
            f"GPU, самый загруженный движок — {gpu}",
            f"Сеть — приём {last['rx']:.0f}, передача {last['tx']:.0f} КиБ/с",
            "\nНаибольший рабочий набор памяти",
            *(
                f"{name} — {size:.0f} МиБ"
                for size, name in sorted(apps, reverse=True)[:6]
            ),
            "\nЗаписи автозапуска в разделах Run",
            *sorted(startup),
            "",
            "Замер длится около секунды. Краткий снимок не устанавливает причину постоянных торможений.",
        ]
    )
    return summary, report
