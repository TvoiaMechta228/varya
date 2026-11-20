"""Таймеры и поиск по локальным заметкам в окне Вари."""

import tkinter as tk
from tkinter import scrolledtext, ttk

import features
import settings


class Organizer(ttk.Frame):
    """Инструменты, которыми можно пользоваться и при включённом микрофоне."""

    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent, padding=18)
        self.feedback = tk.StringVar()
        ttk.Label(self, textvariable=self.feedback, wraplength=580).pack(
            side="bottom", anchor="w", pady=5,
        )
        ttk.Label(self, text="Таймеры", font=("Segoe UI", 15, "bold")).pack(
            anchor="w",
        )
        row = ttk.Frame(self)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="Название").pack(side="left")
        self.name = ttk.Entry(row, width=18)
        self.name.pack(side="left", padx=5)
        ttk.Label(row, text="Минуты").pack(side="left")
        self.minutes = ttk.Spinbox(row, from_=1, to=1440, width=5)
        self.minutes.set("5")
        self.minutes.pack(side="left", padx=5)
        ttk.Button(row, text="Запустить", command=self.start_timer).pack(side="left")
        self.timers = ttk.Treeview(
            self, columns=("label", "time"), show="headings", height=4,
            selectmode="browse",
        )
        self.timers.heading("label", text="Название")
        self.timers.heading("time", text="Осталось")
        self.timers.column("time", width=100, stretch=False)
        self.timers.pack(fill="x")
        ttk.Button(self, text="Отменить выбранный", command=self.cancel).pack(
            anchor="e", pady=4,
        )

        ttk.Label(self, text="Заметки", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=5)
        self.note = ttk.Entry(row)
        self.note.pack(side="left", expand=True, fill="x")
        self.note.bind("<Return>", lambda _: self.save_note())
        ttk.Button(row, text="Сохранить заметку", command=self.save_note).pack(side="right")
        row = ttk.Frame(self)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text="Найти в заметках").pack(side="left", padx=(0, 8))
        self.query = tk.StringVar()
        ttk.Entry(row, textvariable=self.query).pack(fill="x", expand=True)
        self.results = scrolledtext.ScrolledText(
            self, height=5, wrap="word", state="disabled", font=("Segoe UI", 11),
        )
        self.results.pack(fill="both", expand=True)
        self.cache_key = None
        self.query.trace_add("write", lambda *_: self.refresh_notes())
        self.bind("<Map>", lambda _: self.refresh())

    def start_timer(self) -> None:
        """Создать таймер по введённой длительности."""
        try:
            seconds = int(self.minutes.get()) * 60
        except ValueError:
            self.feedback.set("Введите целое число минут от 1 до 1440.")
            return
        try:
            name = features.add_timer(self.name.get(), seconds)
        except ValueError as error:
            self.feedback.set(str(error))
            return
        self.name.delete(0, "end")
        self.feedback.set(f"Таймер «{name}» запущен.")
        self.refresh()

    def cancel(self) -> None:
        """Отменить выбранный таймер."""
        selected = self.timers.selection()
        if not selected:
            self.feedback.set("Выберите таймер в списке.")
            return
        self.feedback.set("Таймер отменён." if features.cancel_timer(selected[0])
                          else "Таймер уже завершён.")
        self.refresh()

    def save_note(self) -> None:
        """Сохранить введённый текст и обновить список."""
        try:
            features.remember(self.note.get())
        except (ValueError, OSError) as error:
            self.feedback.set(f"Не удалось сохранить заметку. {error}")
            return
        self.note.delete(0, "end")
        self.query.set("")
        self.feedback.set("Заметка сохранена.")
        self.refresh_notes()

    def refresh(self) -> None:
        """Обновить время, сохранив выделение пользователя."""
        rows = features.remaining()
        names = {name for name, _ in rows}
        for old in self.timers.get_children():
            if old not in names:
                self.timers.delete(old)
        for name, seconds in rows:
            hours, seconds = divmod(seconds, 3600)
            minutes, seconds = divmod(seconds, 60)
            values = (name, f"{hours:02}:{minutes:02}:{seconds:02}")
            if self.timers.exists(name):
                self.timers.item(name, values=values)
            else:
                self.timers.insert("", "end", iid=name, values=values)
        self.refresh_notes()

    def refresh_notes(self) -> None:
        """Перечитать заметки только при изменении файла или запроса."""
        path = settings.DATA_DIR / "notes.json"
        try:
            try:
                stamp = path.stat().st_mtime_ns
            except FileNotFoundError:
                stamp = None
            query = self.query.get().strip().casefold()
            key = (stamp, query)
            if key == self.cache_key:
                return
            self.cache_key = key
            notes = features.read_notes()
            lines = [entry["text"] for entry in reversed(notes)
                     if query in entry["text"].casefold()]
            content = "\n\n".join(lines) or (
                "Совпадений нет." if query else "Пока нет заметок."
            )
        except (OSError, ValueError):
            content = "Не удалось прочитать файл заметок."
        self.results.configure(state="normal")
        self.results.delete("1.0", "end")
        self.results.insert("1.0", content)
        self.results.configure(state="disabled")
