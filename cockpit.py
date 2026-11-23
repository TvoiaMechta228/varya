"""Панели командного центра и анимация состояния голосовой сессии."""

import json
import math
import threading
import time
import tkinter as tk
from collections import deque
from queue import Empty, Queue
from tkinter import ttk
from urllib.request import ProxyHandler, build_opener

import appearance as ui
import conversation
import services
import telemetry


def label(parent, text, size=10, color=None, bold=False):
    return tk.Label(
        parent,
        text=text,
        anchor="w",
        bd=0,
        bg=parent["bg"],
        fg=color or ui.INK,
        font=("Segoe UI Semibold" if bold else "Segoe UI", size),
    )


class Reactor(tk.Canvas):
    """Параметрический индикатор. Скорость и цвет следуют состоянию речи."""

    def __init__(self, parent, status, *, compact=False):
        super().__init__(
            parent,
            bg=ui.PANEL,
            bd=0,
            highlightthickness=0,
            height=62 if compact else 210,
        )
        self.status = status
        self.compact = compact
        self.level = 0.0
        self.phase = 0
        self.last_idle = None
        self.bind("<Map>", self.start)
        self.bind("<Unmap>", self.stop)
        self.bind("<Destroy>", self.stop)
        self.timer = None

    def stop(self, _=None):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None

    def start(self, _=None):
        self.stop()
        self.draw()

    def draw(self):
        self.timer = None
        w, h = self.winfo_width(), self.winfo_height()
        cx, cy = w / 2, h / 2
        r = min(w * 0.38, h * 0.44, 155)
        state = self.status.get().lower()
        listening = any(k in state for k in ("слушаю", "запис", "микрофон…"))
        speaking = "озвучиваю" in state
        active = (
            listening
            or speaking
            or any(k in state for k in ("обрабатываю", "модель", "пишу", "измеряю"))
        )
        idle = (w, h, state)
        if not active and self.last_idle == idle:
            self.timer = self.after(500, self.draw)
            return
        self.last_idle = None if active else idle
        self.phase += 1.8 if active else 0.42
        angle = self.phase
        accent = "#ffbd70" if speaking else "#57edde"
        self.delete("all")
        for scale, fill in (
            (1.1, "#101f2b"),
            (0.92, "#112833"),
            (0.72, "#10323d"),
            (0.49, "#11404b"),
        ):
            size = r * scale
            self.create_oval(
                cx - size, cy - size, cx + size, cy + size, fill=fill, outline=""
            )
        if not self.compact:
            for side in (-1, 1):
                self.create_line(
                    cx + side * r * 1.06,
                    cy,
                    cx + side * (r + 18),
                    cy,
                    cx + side * (r + 31),
                    cy - 12,
                    cx + side * (r + 62),
                    cy - 12,
                    fill="#375d67",
                )
                for y in (-24, -12, 0, 12, 24):
                    self.create_line(
                        cx + side * (r + 42),
                        cy + y,
                        cx + side * (r + 46),
                        cy + y,
                        fill="#29505b",
                    )
        for i in range(72):
            a = math.radians(i * 5)
            low = r * (0.95 if i % 3 else 0.90)
            self.create_line(
                cx + low * math.cos(a),
                cy + low * math.sin(a),
                cx + r * math.cos(a),
                cy + r * math.sin(a),
                fill="#66858d" if i % 3 == 0 else "#284852",
            )
        for scale, segments, span, width, speed, color in (
            (0.84, 3, 86, 3, 1, accent),
            (0.76, 6, 38, 1, -1.2, "#407888"),
            (0.63, 3, 83, 5, -0.7, "#428e9a"),
            (0.55, 2, 150, 1, 1.4, "#92ebdf"),
        ):
            size = r * scale
            for i in range(segments):
                self.create_arc(
                    cx - size,
                    cy - size,
                    cx + size,
                    cy + size,
                    style="arc",
                    start=angle * speed + i * 360 / segments,
                    extent=span,
                    outline=color,
                    width=width,
                )
        # Сферическая сетка и светящееся ядро.
        self.level *= 0.90
        size = r * (
            0.37
            + 0.10 * self.level
            + (0.035 if active else 0.012) * math.sin(math.radians(angle * 4))
        )
        self.create_oval(
            cx - size, cy - size, cx + size, cy + size, fill="#164751", outline=accent
        )
        for k in range(5):
            rad = math.radians(angle + k * 36)
            coords = []
            for j in range(37):
                t = j * math.pi / 18
                x, y = size * 0.98 * math.cos(t), size * 0.30 * math.sin(t)
                coords.extend(
                    (
                        cx + x * math.cos(rad) - y * math.sin(rad),
                        cy + x * math.sin(rad) + y * math.cos(rad),
                    )
                )
            self.create_line(coords, fill="#4c9caa", smooth=True)
        for frac, color in ((0.18, "#397c85"), (0.12, accent), (0.055, "#effff9")):
            rad = r * frac
            self.create_oval(
                cx - rad, cy - rad, cx + rad, cy + rad, fill=color, outline=""
            )
        self.timer = self.after(100 if active else 500, self.draw)


class Panels:
    """Телеметрия в фоновом потоке и быстрые задачи с обычной маршрутизацией."""

    def __init__(self, parent, submit, open_log):
        self.left = tk.Frame(parent, bg=ui.PANEL, padx=14, pady=14)
        self.right = tk.Frame(parent, bg=ui.PANEL, padx=14, pady=14)
        self.metrics = {}
        self.traces = {k: deque(maxlen=34) for k in ("cpu", "gpu", "ram", "disk")}
        label(self.left, "Состояние ПК", 13, bold=True).pack(anchor="w", pady=(0, 12))
        for key, title in (
            ("cpu", "Процессор"),
            ("gpu", "Графика"),
            ("ram", "Память ПК"),
            ("disk", "Системный диск"),
        ):
            line = tk.Frame(self.left, bg=ui.PANEL)
            line.pack(fill="x", pady=(6, 0))
            label(line, title, 10, ui.MUTED).pack(side="left")
            val = label(line, "…", 11, bold=True)
            val.pack(side="right")
            graph = tk.Canvas(
                self.left, width=152, height=28, bg=ui.PANEL, highlightthickness=0
            )
            graph.pack(fill="x", pady=(2, 5))
            self.metrics[key] = val, graph
        label(self.left, "Сеть", 10, ui.MUTED).pack(anchor="w", pady=(8, 1))
        self.network = label(self.left, "Измеряю…", 10)
        self.network.pack(anchor="w")
        self.app_memory = label(self.left, "Варя · измеряю память", 9, ui.MUTED)
        self.app_memory.pack(anchor="w", pady=(12, 0))
        label(self.right, "Быстрые задачи", 13, bold=True).pack(
            anchor="w", pady=(0, 12)
        )
        for title, cmd in (
            ("Диагностика ПК", "проверь компьютер"),
            ("Разобрать стол", "наведи порядок на рабочем столе"),
            ("Начать работу", "подготовь меня к работе"),
            ("Управлять окном", "покажи кнопки"),
        ):
            ttk.Button(self.right, text=title, command=lambda c=cmd: submit(c)).pack(
                fill="x", pady=3
            )
        label(self.right, "Сервисы", 12, bold=True).pack(anchor="w", pady=(17, 5))
        self.service = label(self.right, "Ollama  ·  проверка", 10, ui.MUTED)
        self.service.pack(anchor="w")
        label(
            self.right,
            "OneDrive  ·  "
            + ("папка доступна" if services.cloud_folder() else "не подключён"),
            10,
            ui.MUTED,
        ).pack(anchor="w")
        ttk.Button(self.right, text="Журнал действий", command=open_log).pack(
            anchor="w", pady=(15, 3)
        )
        self.last = label(self.right, "", 9, ui.MUTED)
        self.last.configure(wraplength=160, justify="left")
        self.last.pack(anchor="w")
        self.queue = Queue(maxsize=2)
        self.closed = threading.Event()
        self.visible = threading.Event()
        self.left.bind("<Map>", lambda _: self.visible.set())
        self.left.bind("<Unmap>", lambda _: self.visible.clear())
        self.left.bind(
            "<Destroy>", lambda e: self.closed.set() if e.widget is self.left else None
        )
        threading.Thread(
            target=self.collect, daemon=True, name="VaryaTelemetry"
        ).start()

    def collect(self):
        model = "проверка"
        monitor = None
        next_check = 0
        opener = build_opener(ProxyHandler({}))
        try:
            monitor = telemetry.Monitor()
            while not self.closed.wait(2):
                if not self.visible.is_set():
                    continue
                if time.monotonic() >= next_check:
                    try:
                        with opener.open(
                            "http://127.0.0.1:11434/api/tags", timeout=0.65
                        ) as response:
                            models = json.loads(response.read(512000))["models"]
                        model = (
                            "готова"
                            if any(m.get("name") == conversation.MODEL for m in models)
                            else "нет модели"
                        )
                    except (ValueError, OSError, KeyError, TypeError):
                        model = "недоступна"
                    next_check = time.monotonic() + 20
                if not self.queue.full():
                    self.queue.put_nowait((monitor.sample(), model))
        except (OSError, RuntimeError):
            if not self.queue.full():
                self.queue.put_nowait(({}, model))
        finally:
            if monitor:
                monitor.close()

    def refresh(self):
        try:
            data, model = self.queue.get_nowait()
        except Empty:
            return
        self.service.config(text="Ollama  ·  " + model)
        if "app_memory" in data:
            self.app_memory.config(text=f"Варя · {data['app_memory']:.0f} МиБ")
        for key, (value, graph) in self.metrics.items():
            n = data.get(key)
            value.config(text="н/д" if n is None else f"{n:.0f}%")
            graph.delete("all")
            width = max(70, graph.winfo_width())
            graph.create_line(0, 25, width, 25, fill=ui.EDGE)
            if n is None:
                continue
            self.traces[key].append(n)
            pts = [
                (width - (len(self.traces[key]) - i - 1) * width / 33, 23 - v * 0.21)
                for i, v in enumerate(self.traces[key])
            ]
            if len(pts) > 1:
                graph.create_line(pts, fill=ui.ACCENT, width=2)
            graph.create_oval(
                width - 4,
                pts[-1][1] - 2,
                width,
                pts[-1][1] + 2,
                fill=ui.ACCENT,
                outline="",
            )
        self.network.config(
            text=(
                f"↓ {data['rx']:.0f}  ↑ {data['tx']:.0f} КиБ/с"
                if data
                else "нет показаний"
            )
        )
