"""Палитра Вари, голосовой индикатор и оформление стандартных элементов Tk."""

import math
import tkinter as tk
from tkinter import ttk

BG = "#0a141e"
PANEL = "#101f2b"
FIELD = "#19313d"
INK = "#eaf4f5"
MUTED = "#9bb3bd"
ACCENT = "#64e4d3"
EDGE = "#28414b"


def theme(root: tk.Misc) -> None:
    """Применить графитовую тему ко всем вкладкам и состояниям клавиатуры."""
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(bg=BG)
    style.configure(".", background=BG, foreground=INK, font=("Segoe UI", 10),
                    bordercolor=EDGE, lightcolor=EDGE, darkcolor=EDGE,
                    troughcolor=PANEL, focuscolor=ACCENT)
    style.configure("TNotebook", tabmargins=(0, 0, 0, 6), borderwidth=0)
    style.configure("TNotebook.Tab", padding=(14, 8), foreground=MUTED)
    style.map("TNotebook.Tab", background=[("selected", FIELD)], foreground=[("selected", INK)])
    style.configure("Title.TLabel", font=("Segoe UI Semibold", 23), foreground=INK)
    style.configure("Quiet.TLabel", foreground=MUTED)
    style.configure("TButton", padding=(12, 7), background=FIELD, borderwidth=1)
    style.map("TButton", background=[("pressed", "#254f57"), ("active", "#24404d")],
              foreground=[("disabled", "#788d96")])
    style.configure("Accent.TButton", background=ACCENT, foreground=BG)
    style.map("Accent.TButton", background=[("disabled", FIELD), ("active", "#99f9e7")],
              foreground=[("disabled", MUTED)])
    for widget in ("TEntry", "TSpinbox", "Treeview"):
        style.configure(widget, fieldbackground=PANEL, foreground=INK, insertcolor=INK)
    style.configure("Treeview", background=PANEL, rowheight=28)
    style.configure("Treeview.Heading", background=FIELD)
    style.map("Treeview", background=[("selected", "#284d59")])
    for widget in ("TCheckbutton", "TRadiobutton"):
        style.map(widget, background=[("active", PANEL)])
    style.configure("Vertical.TScrollbar", background=FIELD, arrowsize=12)


def wave(canvas: tk.Canvas, cx: int, cy: int, size: float = 1) -> None:
    """Нарисовать голосовую волну из трёх линий с огибающей."""
    for amplitude, phase, color in ((19, 1.1, "#776295"), (35, 0, ACCENT), (25, 2.1, "#9d86bf")):
        points = []
        for step in range(121):
            x = (step - 60) / 60
            y = math.cos(x * math.pi / 2) ** 2 * math.sin(x * 7 + phase)
            points.extend((cx + x * 99 * size, cy + y * amplitude * size))
        canvas.create_line(points, fill=color, width=max(1, round(2 * size)), smooth=True)

