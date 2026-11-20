"""Оконное приложение: Tk в главном потоке, речь и COM — в рабочем."""

from datetime import datetime
import logging
from queue import Empty, Queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import config
import main
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
        if record.name in {"main", "speech", "safety", "actions", __name__}:
            self.events.put(("log", record.getMessage()))


class AssistantController:
    """Управлять одной речевой сессией и безопасной остановкой."""

    def __init__(self, events: Queue) -> None:
        """Создать постоянный поток: SAPI5 не переносится между потоками."""
        self.events = events
        self.request = threading.Event()
        self.stopping = threading.Event()
        self.closing = threading.Event()
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

    def _run(self) -> None:
        """Последовательно слушать и выполнять только актуальные команды."""
        import comtypes

        comtypes.CoInitialize()
        speech.cancel_event = self.stopping
        try:
            while not self.closing.is_set():
                if not self.request.wait(0.1):
                    continue
                self.request.clear()
                if self.closing.is_set():
                    break
                try:
                    self.events.put(("state", "Отвечаю…"))
                    speech.speak("Я Варя. Слушаю ваши команды.")
                    while not self.stopping.is_set():
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
        root.geometry("680x600")
        root.minsize(600, 540)
        root.configure(bg="#f3f5fa")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#f3f5fa")
        style.configure(
            "TLabel", background="#f3f5fa", font=("Segoe UI", 11),
        )
        style.configure("Title.TLabel", font=("Segoe UI", 30, "bold"))
        style.configure("TButton", font=("Segoe UI", 11), padding=10)
        frame = ttk.Frame(root, padding=26)
        frame.pack(fill="both", expand=True)
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
        ttk.Label(
            frame, text="Например: «который час», «открой блокнот», «скриншот»",
            wraplength=580,
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

    def poll(self) -> None:
        """Обработать очередь в главном потоке Tk."""
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind == "log":
                    self.journal.configure(state="normal")
                    self.journal.insert("end", f"{datetime.now():%H:%M}  {text}\n")
                    if int(self.journal.index("end-1c").split(".")[0]) > 400:
                        self.journal.delete("1.0", "100.0")
                    self.journal.see("end")
                    self.journal.configure(state="disabled")
                elif kind == "idle" and not self.closing:
                    self.busy = False
                    self.status.set(text)
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                elif kind == "state" and not self.controller.stopping.is_set():
                    self.status.set(text)
                elif kind == "exit":
                    self.close()
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
