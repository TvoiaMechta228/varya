"""Оконное приложение: Tk в главном потоке, речь и COM — в рабочем."""

import logging
from queue import Empty, Queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import conversation
from dialogue import Dialogue
import features
import main
from organizer import Organizer
import settings
import speech

logger = logging.getLogger(__name__)


class AssistantController:
    """Управлять одной речевой сессией и безопасной остановкой."""

    def __init__(self, events: Queue) -> None:
        """Создать постоянный поток: SAPI5 не переносится между потоками."""
        self.events = events
        self.stopping = threading.Event()
        self.closing = threading.Event()
        self.commands: Queue = Queue()
        self.alarms: Queue = Queue()
        self.thread = threading.Thread(
            target=self._run, name="VaryaSpeech", daemon=True,
        )
        self.thread.start()

    def start(self) -> None:
        """Запросить начало сессии."""
        self.stopping.clear()
        self.commands.put(("start", ""))

    def stop(self) -> None:
        """Отменить текущую команду после завершения ввода/сети."""
        self.stopping.set()
        self.commands.put(("stop", ""))

    def close(self) -> None:
        """Запросить остановку рабочего потока."""
        self.closing.set()
        self.stopping.set()
        self.commands.put(("close", ""))

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
        speech.shutdown_event = self.closing
        token = speech.transcript.set(lambda kind, text: self.events.put((kind, text)))
        dialogue = Dialogue()
        listening = False
        try:
            while not self.closing.is_set():
                speech.cancel_event = self.stopping if listening else self.closing
                self.announce_timers()
                try:
                    kind, text = self.commands.get(timeout=0 if listening else 0.15)
                except Empty:
                    if not listening or self.stopping.is_set():
                        continue
                    kind, text = "voice", ""
                if kind == "close":
                    break
                if kind == "stop":
                    listening = False
                    dialogue = Dialogue()
                    self.events.put(("stopped", "Микрофон выключен"))
                    continue
                if kind == "preview":
                    speech.speak("Привет! Я Варя. Как меня слышно?")
                    continue
                speech.cancel_event = self.stopping
                if speech.is_cancelled():
                    continue
                if kind == "start":
                    speech.new_session()
                    listening = True
                    continue
                try:
                    if kind == "voice":
                        text = speech.listen()
                    if text and not speech.is_cancelled():
                        self.events.put(("state", "Обрабатываю сообщение…"))
                        if kind == "typed":
                            logger.info("Введено: %s", text)
                        if not main.route_command(text, dialogue=dialogue, typed=kind == "typed"):
                            self.events.put(("exit", ""))
                            break
                except Exception:
                    logger.exception("Ошибка обработки сообщения")
                    self.events.put(("assistant", "Не получилось обработать сообщение. Попробуй ещё раз."))
                finally:
                    if kind == "typed":
                        self.events.put(("sent", ""))
        finally:
            speech.transcript.reset(token)
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
        self.listening = False
        self.sending = False
        self.stopping = False
        conversation.enabled = True
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
        header = ttk.Frame(frame)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="Варя", style="Title.TLabel").pack(side="left")
        self.status = tk.StringVar(value="Микрофон выключен")
        ttk.Label(header, textvariable=self.status).pack(side="right")
        self.journal = scrolledtext.ScrolledText(
            frame, wrap="word", font=("Segoe UI", 12), bg="white", fg="#192336",
            relief="flat", padx=22, pady=16, height=10, state="disabled",
        )
        self.journal.pack(fill="both", expand=True)
        for role, color in (("user", "#485265"), ("assistant", "#3151a5")):
            self.journal.tag_configure(role, foreground=color, spacing1=10,
                                       font=("Segoe UI", 10, "bold"))
        self.drafting = False
        self.suggestions = ttk.Frame(frame)
        self.suggestions.pack(fill="x", pady=(10, 0))
        for title, prompt in (("Поставить таймер", "поставь таймер"),
                              ("Записать заметку", "запиши заметку"),
                              ("Найти в интернете", "найди в интернете")):
            ttk.Button(self.suggestions, text=title, command=lambda p=prompt:
                       self.submit_command(p)).pack(side="left", padx=(0, 6))
        ttk.Label(frame, text="Сообщение Варе").pack(anchor="w", pady=(14, 4))
        entry_row = ttk.Frame(frame)
        entry_row.pack(fill="x")
        self.command_text = tk.StringVar()
        self.entry = ttk.Entry(entry_row, textvariable=self.command_text, font=("Segoe UI", 12))
        self.entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))
        self.entry.bind("<Return>", lambda event: self.submit_command())
        self.send_button = ttk.Button(entry_row, text="Отправить", command=self.submit_command)
        self.send_button.pack(side="right")
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(14, 0))
        self.start_button = ttk.Button(controls, text="Включить микрофон", command=self.start)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(controls, text="Остановить", command=self.stop,
                                      state="disabled")
        self.stop_button.pack(side="left")
        self.entry.focus_set()
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

    def submit_command(self, suggestion: str | None = None) -> None:
        """Поставить одно сообщение в очередь, сохранив текущую голосовую сессию."""
        text = suggestion or self.command_text.get().strip()
        if not text or self.closing or self.stopping or self.sending:
            return
        self.sending = self.busy = True
        self.controller.stopping.clear()
        self.start_button.configure(state="disabled")
        self.send_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Сообщение в очереди…")
        self.controller.commands.put(("typed", text))
        if suggestion is None:
            self.command_text.set("")
        self.entry.focus_set()

    def preview_voice(self) -> None:
        """Озвучить образец в том же рабочем потоке, что и ответы."""
        if self.busy:
            messagebox.showinfo("Голос", "Сначала остановите прослушивание.")
        elif self.save_settings():
            self.controller.commands.put(("preview", ""))

    def start(self) -> None:
        """Включить прослушивание после нажатия пользователем."""
        if self.busy or self.closing:
            return
        self.listening = self.busy = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Запускаюсь…")
        self.controller.start()

    def stop(self) -> None:
        """Остановить сессию и отбросить ещё не выполненную реплику."""
        self.stopping = True
        self.send_button.configure(state="disabled")
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

    def append_turn(self, role: str, text: str) -> None:
        """Показать текст модели по мере генерации и заменить черновик полным ответом."""
        if role == "user":
            self.suggestions.pack_forget()
        view = self.journal
        view.configure(state="normal")
        if self.drafting and role != "delta":
            view.delete("draft", "end-1c")
            self.drafting = False
        if role != "end_draft":
            if not self.drafting:
                if role == "delta":
                    view.mark_set("draft", "end-1c")
                    view.mark_gravity("draft", "left")
                    self.drafting = True
                who = "user" if role == "user" else "assistant"
                view.insert("end", ("Вы" if who == "user" else "Варя") + "\n", who)
            view.insert("end", text if role == "delta" else text + "\n\n")
        if not self.drafting and int(view.index("end-1c").split(".")[0]) > 500:
            view.delete("1.0", "100.0")
        view.see("end")
        view.configure(state="disabled")

    def poll(self) -> None:
        """Обработать очередь в главном потоке Tk."""
        try:
            while True:
                kind, text = self.events.get_nowait()
                if kind in {"user", "assistant", "delta", "end_draft"}:
                    self.append_turn(kind, text)
                elif kind in {"sent", "stopped"} and not self.closing:
                    if kind == "sent" and self.stopping:
                        continue
                    if kind == "stopped":
                        self.listening = self.stopping = False
                    self.sending = False
                    self.busy = self.listening
                    self.send_button.configure(state="normal")
                    self.stop_button.configure(state="normal" if self.busy else "disabled")
                    self.start_button.configure(state="disabled" if self.busy else "normal")
                    self.status.set("Слушаю…" if self.listening else "Микрофон выключен")
                elif kind == "state" and not self.stopping:
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
