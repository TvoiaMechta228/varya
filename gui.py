"""Оконное приложение: Tk в главном потоке, речь и COM — в рабочем."""

from datetime import datetime
import logging
from queue import Empty, Queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import config
import features
import main
from organizer import Organizer
import settings
import speech

logger = logging.getLogger(__name__)


class QueueLogHandler(logging.Handler):
    """Передавать записи в окно без вызовов Tk из рабочего потока."""

    def __init__(self, events: Queue) -> None:
        """Запомнить очередь событий интерфейса."""
        super().__init__(logging.INFO)
        self.events = events

    def emit(self, record: logging.LogRecord) -> None:
        """Добавить сообщение в очередь."""
        if record.name in {
            "main", "speech", "safety", "actions", "features", __name__,
        }:
            self.events.put(("log", record.getMessage()))


class AssistantController:
    """Управлять одной речевой сессией и безопасной остановкой."""

    def __init__(self, events: Queue) -> None:
        """Создать постоянный поток: SAPI5 не переносится между потоками."""
        self.events = events
        self.request = threading.Event()
        self.stopping = threading.Event()
        self.closing = threading.Event()
        self.preview = threading.Event()
        self.commands: Queue = Queue()
        self.alarms: Queue = Queue()
        self.thread = threading.Thread(
            target=self._run, name="VaryaSpeech", daemon=True,
        )
        self.thread.start()

    def start(self) -> None:
        """Запросить начало сессии."""
        self.stopping.clear()
        self.request.set()

    def stop(self) -> None:
        """Отменить текущую команду после завершения ввода/сети."""
        self.stopping.set()

    def close(self) -> None:
        """Запросить остановку рабочего потока."""
        self.closing.set()
        self.stopping.set()
        self.request.set()

    def announce_timers(self) -> None:
        """Озвучить сигнал, когда освободится речевой поток."""
        try:
            message = self.alarms.get_nowait()
        except Empty:
            return
        speech.speak(message)

    def _run(self) -> None:
        """Последовательно слушать и выполнять только актуальные команды."""
        import comtypes

        comtypes.CoInitialize()
        speech.cancel_event = self.stopping
        speech.shutdown_event = self.closing
        try:
            while not self.closing.is_set():
                speech.cancel_event = self.closing
                try:
                    typed = self.commands.get_nowait()
                except Empty:
                    pass
                else:
                    speech.cancel_event = self.stopping
                    self.events.put(("busy", "Выполняю введённую команду…"))
                    logger.info("Введено: %s", typed)
                    if (not self.stopping.is_set()
                            and not main.route_command(typed)):
                        self.events.put(("exit", ""))
                    self.events.put(("idle", "Готова к работе"))
                    speech.cancel_event = self.closing
                if self.preview.is_set():
                    self.preview.clear()
                    speech.speak("Привет! Я Варя, твоя помощница. "
                                 "Давай сделаем этот день немного проще.")
                self.announce_timers()
                if not self.request.wait(0.1):
                    continue
                self.request.clear()
                if self.closing.is_set():
                    break
                speech.cancel_event = self.stopping
                try:
                    self.events.put(("state", "Отвечаю…"))
                    speech.speak("Я Варя. Слушаю ваши команды.")
                    while not self.stopping.is_set():
                        self.announce_timers()
                        self.events.put(("state", "Слушаю микрофон…"))
                        text = speech.listen()
                        if self.stopping.is_set():
                            break
                        if text:
                            self.events.put(("state", "Выполняю команду…"))
                            if not main.route_command(text):
                                self.events.put(("exit", ""))
                                self.stopping.set()
                except Exception:
                    logger.exception("Ошибка рабочего потока")
                finally:
                    self.events.put(("idle", "Готова к работе"))
        finally:
            speech.close()
            speech.cancel_event = None
            speech.shutdown_event = None
            comtypes.CoUninitialize()


class VaryaWindow:
    """Главное окно с управлением микрофоном и журналом."""

    def __init__(self, root: tk.Tk) -> None:
        """Построить интерфейс без автоматического включения микрофона."""
        self.root = root
        self.closing = False
        self.busy = False
        self.events: Queue = Queue()
        self.handler = QueueLogHandler(self.events)
        logging.getLogger().addHandler(self.handler)
        self.controller = AssistantController(self.events)
        root.title("Варя — голосовой ассистент")
        root.geometry("780x750")
        root.minsize(680, 640)
        root.configure(bg="#f3f5fa")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5fa")
        style.configure(
            "TLabel", background="#f3f5fa", font=("Segoe UI", 11),
        )
        style.configure("Title.TLabel", font=("Segoe UI", 30, "bold"))
        style.configure("TButton", font=("Segoe UI", 11), padding=10)
        tabs = ttk.Notebook(root)
        tabs.pack(fill="both", expand=True, padx=16, pady=16)
        frame = ttk.Frame(tabs, padding=20)
        tabs.add(frame, text="  Ассистент  ")
        self.organizer = Organizer(tabs)
        tabs.add(self.organizer, text="  Мои дела  ")
        self.make_settings(tabs)
        commands = ttk.Frame(tabs, padding=20)
        tabs.add(commands, text="  Команды  ")
        guide = scrolledtext.ScrolledText(
            commands, wrap="word", font=("Segoe UI", 12), padx=12, pady=12,
        )
        guide.pack(fill="both", expand=True)
        guide.insert("1.0", features.HELP)
        guide.configure(state="disabled")
        ttk.Label(frame, text="Варя", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Ваш голосовой помощник для Windows").pack(
            anchor="w", pady=(0, 18),
        )
        self.status = tk.StringVar(value="Готова к работе")
        ttk.Label(frame, textvariable=self.status).pack(anchor="w")
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=16)
        self.start_button = ttk.Button(
            controls, text="Начать слушать", command=self.start,
        )
        self.start_button.pack(side="left", padx=(0, 10))
        self.stop_button = ttk.Button(
            controls, text="Остановить", command=self.stop, state="disabled",
        )
        self.stop_button.pack(side="left")
        entry_row = ttk.Frame(frame)
        entry_row.pack(fill="x", pady=(0, 12))
        self.command_text = tk.StringVar()
        entry = ttk.Entry(entry_row, textvariable=self.command_text,
                          font=("Segoe UI", 11))
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        entry.bind("<Return>", lambda event: self.submit_command())
        ttk.Button(entry_row, text="Выполнить",
                   command=self.submit_command).pack(side="right")
        ttk.Label(
            frame, text="«Варя, таймер на пять минут» • "
            "«запомни купить молоко»",
            wraplength=640,
        ).pack(anchor="w", pady=(0, 12))
        self.journal = scrolledtext.ScrolledText(
            frame, height=12, wrap="word", font=("Segoe UI", 10),
            bg="white", relief="flat", padx=12, pady=12, state="disabled",
        )
        ttk.Label(
            frame, text="Микрофон включается кнопкой. Выключение ПК — "
            "только с голосовым подтверждением.", wraplength=580,
        ).pack(side="bottom", anchor="w", pady=(12, 0))
        self.journal.pack(fill="both", expand=True)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)
        self.tick()

    def make_settings(self, tabs: ttk.Notebook) -> None:
        """Добавить сохраняемые настройки голоса и обращения по имени."""
        panel = ttk.Frame(tabs, padding=24)
        tabs.add(panel, text="  Голос и настройки  ")
        options = settings.snapshot()
        ttk.Label(panel, text="Голос Вари", style="Title.TLabel").pack(
            anchor="w", pady=(0, 18),
        )
        self.voice = tk.StringVar(value=options["voice"])
        for title, value in (
            ("Эмма — нейросетевой женский голос (интернет)", "neural"),
            ("Ирина — локальный русский голос Windows", "sapi"),
        ):
            ttk.Radiobutton(panel, text=title, value=value,
                            variable=self.voice).pack(anchor="w", pady=6)
        self.rate = tk.IntVar(value=options["rate"])
        self.volume = tk.IntVar(value=options["volume"])
        for title, variable, low, high in (
            ("Скорость речи", self.rate, 120, 230),
            ("Громкость голоса, %", self.volume, 10, 100),
        ):
            row = ttk.Frame(panel)
            row.pack(fill="x", pady=12)
            ttk.Label(row, text=title).pack(side="left")
            ttk.Spinbox(row, from_=low, to=high, textvariable=variable,
                        width=7).pack(side="right")
        self.wake_word = tk.BooleanVar(value=options["wake_word"])
        ttk.Checkbutton(
            panel, text="Выполнять команды только со слова «Варя»",
            variable=self.wake_word,
        ).pack(anchor="w", pady=14)
        ttk.Label(
            panel, text="Обращение по имени проверяется после распознавания. "
            "Микрофон всё равно слушает во время включённой сессии.\n\n"
            "Для голоса Эмма текст ответа передаётся Microsoft. "
            "При сбое включается голос Windows. Звук не сохраняется.",
            wraplength=610,
        ).pack(anchor="w", pady=12)
        ttk.Button(panel, text="Сохранить настройки",
                   command=self.save_settings).pack(anchor="w", pady=8)
        self.preview_button = ttk.Button(
            panel, text="Послушать голос", command=self.preview_voice,
        )
        self.preview_button.pack(anchor="w", pady=8)

    def save_settings(self) -> bool:
        """Проверить поля и сохранить без прямых вызовов аудио из Tk."""
        try:
            settings.save({
                "voice": self.voice.get(), "rate": self.rate.get(),
                "volume": self.volume.get(), "wake_word": self.wake_word.get(),
            })
        except (OSError, ValueError, tk.TclError) as error:
            messagebox.showerror("Настройки", str(error), parent=self.root)
            return False
        logger.info("Настройки сохранены")
        return True

    def submit_command(self) -> None:
        """Передать введённую команду в последовательную очередь действий."""
        if self.closing:
            return
        if self.busy:
            messagebox.showinfo("Команда", "Сначала остановите микрофон.")
            return
        text = self.command_text.get().strip()
        if text:
            self.busy = True
            self.controller.stopping.clear()
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.controller.commands.put(text)
            self.command_text.set("")

    def preview_voice(self) -> None:
        """Озвучить образец в том же рабочем потоке, что и ответы."""
        if self.busy:
            messagebox.showinfo("Голос", "Сначала остановите прослушивание.")
        elif self.save_settings():
            self.controller.preview.set()

    def start(self) -> None:
        """Включить прослушивание после нажатия пользователем."""
        if self.busy or self.closing:
            return
        self.busy = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Запускаюсь…")
        self.controller.start()

    def stop(self) -> None:
        """Остановить сессию и отбросить ещё не выполненную реплику."""
        self.controller.stop()
        self.stop_button.configure(state="disabled")
        self.status.set("Останавливаюсь… ожидаю запись или сеть")

    def close(self) -> None:
        """Закрыть окно после безопасного завершения рабочего потока."""
        self.closing = True
        self.start_button.configure(state="disabled")
        self.stop()
        self.controller.close()

    def tick(self) -> None:
        """Обновлять отсчёт и показывать сигнал даже при занятом микрофоне."""
        if self.closing:
            return
        names = features.expired_names()
        if names:
            message = "Время вышло. " + ", ".join(names) + "."
            self.events.put(("timer", message))
            self.controller.alarms.put(message)
            self.organizer.feedback.set(message)
        if self.organizer.winfo_ismapped():
            self.organizer.refresh()
        self.root.after(250, self.tick)

    def poll(self) -> None:
        """Обработать очередь в главном потоке Tk."""
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "log":
                    self.journal.configure(state="normal")
                    stamp = datetime.now().strftime("%H:%M")
                    self.journal.insert("end", f"{stamp}  {text}\n")
                    if int(self.journal.index("end-1c").split(".")[0]) > 400:
                        self.journal.delete("1.0", "100.0")
                    self.journal.see("end")
                    self.journal.configure(state="disabled")
                elif kind == "busy" and not self.closing:
                    self.busy = True
                    self.status.set(text)
                    self.start_button.configure(state="disabled")
                    self.stop_button.configure(state="normal")
                elif kind == "idle" and not self.closing:
                    self.busy = False
                    self.status.set(text)
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                elif kind == "state" and not self.controller.stopping.is_set():
                    self.status.set(text)
                elif kind == "exit":
                    self.close()
                elif kind == "timer":
                    if not self.closing:
                        self.root.bell()
                        self.status.set(text)
        except Empty:
            pass
        if self.closing and not self.controller.thread.is_alive():
            logging.getLogger().removeHandler(self.handler)
            self.root.destroy()
            return
        self.root.after(100, self.poll)


def run() -> None:
    """Запустить оконную версию, сообщив об ошибке журнала."""
    root = tk.Tk()
    root.withdraw()
    settings.load()
    try:
        main.setup_logging()
    except OSError as error:
        messagebox.showerror("Варя", f"Не удалось открыть журнал: {error}")
        root.destroy()
        return
    VaryaWindow(root)
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    run()
