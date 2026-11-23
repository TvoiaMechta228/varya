"""Оконное приложение: Tk в главном потоке, речь и COM — в рабочем."""

import logging
import os
from pathlib import Path
from queue import Empty, Queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import appearance as ui
import config
import cockpit
import missions
import workspace_tasks
from tray import Tray
import conversation
from dialogue import Dialogue
import features
import main
from organizer import Organizer
import settings
import speech

logger = logging.getLogger(__name__)


def entry_shortcut(event):
    """Сохранить сочетания редактирования при любой активной раскладке Windows."""
    shortcuts = {65: "<<SelectAll>>", 67: "<<Copy>>", 86: "<<Paste>>", 88: "<<Cut>>"}
    virtual = shortcuts.get(event.keycode)
    if virtual and not event.state & 0x20000:  # AltGr оставляем раскладке.
        event.widget.event_generate(virtual)
        return "break"
    return None


class AssistantController:
    """Управлять одной речевой сессией и безопасной остановкой."""

    def __init__(self, events: Queue) -> None:
        """Создать постоянный поток: SAPI5 не переносится между потоками."""
        self.events = events
        self.stopping = threading.Event()
        self.closing = threading.Event()
        self.background = threading.Event()
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
        device_failed = False

        def publish(kind: str, text: str) -> None:
            nonlocal device_failed
            if kind == "mic_unavailable":
                device_failed = True
            else:
                if kind in {"user", "assistant"}:
                    missions.audit("Запрос" if kind == "user" else "Ответ", text)
                self.events.put((kind, text))

        token = speech.transcript.set(publish)
        dialogue = Dialogue()
        choice_version = 0
        choice_deadline = 0.0
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
                    choice_version += 1
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
                    if kind == "choice":
                        version, text = text
                        kind = "typed"
                        if version != choice_version or time.monotonic() >= choice_deadline:
                            self.events.put(("assistant", "Этот вопрос уже закрыт. Повтори просьбу."))
                            continue
                    if kind == "voice":
                        text = speech.listen()
                    if text and not speech.is_cancelled():
                        choice_version += 1
                        self.events.put(("state", "Обрабатываю сообщение…"))
                        if kind == "typed":
                            logger.info("Введено: %s", text)
                        if not main.route_command(text, dialogue=dialogue, typed=kind == "typed",
                                                  require_wake=self.background.is_set()):
                            self.events.put(("exit", ""))
                            break
                        if dialogue.mission or dialogue.proposal:
                            choice_deadline = (dialogue.mission or dialogue.proposal).expires
                            choices = ("Да", "Нет")
                        elif dialogue.pending == "кнопки выбранного окна":
                            choice_deadline = dialogue.until
                            choices = tuple(str(i + 1) for i in range(len(dialogue.window_choices)))
                        elif dialogue.pending == "duration":
                            choice_deadline = dialogue.until
                            choices = ("На пять минут", "На четверть часа", "На полчаса")
                        else:
                            choices = ()
                        if choices and not speech.is_cancelled():
                            self.events.put(("choices", (choice_version, choice_deadline, choices)))
                except Exception:
                    logger.exception("Ошибка обработки сообщения")
                    self.events.put(("assistant", "Не получилось обработать сообщение. Попробуй ещё раз."))
                finally:
                    if device_failed:
                        listening = device_failed = False
                        self.events.put(("mic_paused", "Введите сообщение с клавиатуры"))
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
        self.tray = None
        root.title("Варя · J.A.R.V.I.S.")
        width = min(1220, root.winfo_screenwidth() - 40)
        height = min(780, root.winfo_screenheight() - 88)
        root.geometry(f"{width}x{height}")
        root.minsize(610, 460)
        ui.theme(root)
        self.tabs = tabs = ttk.Notebook(root, takefocus=True)
        tabs.pack(fill="both", expand=True, padx=16, pady=12)
        frame = ttk.Frame(tabs, padding=(16, 6, 16, 8))
        tabs.add(frame, text="Командный центр")
        self.organizer = Organizer(tabs)
        tabs.add(self.organizer, text="Мои дела")
        self.log_page = ttk.Frame(tabs, padding=14)
        tabs.add(self.log_page, text="Журнал")
        self.log_view = scrolledtext.ScrolledText(self.log_page, wrap="word", state="disabled",
            font=("Segoe UI", 11), relief="flat", padx=15, pady=12, bg=ui.PANEL, fg=ui.INK)
        self.log_view.pack(fill="both", expand=True)
        self.log_page.bind("<Map>", lambda _: self.refresh_log())
        self.make_settings(tabs)
        commands = ttk.Frame(tabs, padding=20)
        tabs.add(commands, text="Команды")
        guide = scrolledtext.ScrolledText(
            commands, wrap="word", font=("Segoe UI", 12), padx=16, pady=12,
            bg=ui.PANEL, fg=ui.INK, relief="flat",
        )
        guide.pack(fill="both", expand=True)
        guide.insert("1.0", features.HELP)
        guide.configure(state="disabled")
        header = ttk.Frame(frame)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="Варя", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=" /  J.A.R.V.I.S.", foreground=ui.ACCENT,
                  font=("Consolas", 12, "bold")).pack(side="left", padx=4)
        self.status = tk.StringVar(value="Микрофон выключен")
        ttk.Label(header, textvariable=self.status, style="Quiet.TLabel").pack(side="right")
        self.light = tk.Label(header, text="●", fg=ui.MUTED, bg=ui.BG)
        self.light.pack(side="right", padx=6)
        self.status.trace_add("write", lambda *_: self.light.configure(
            fg=ui.ACCENT if self.busy else ui.MUTED))
        entry_row = ttk.Frame(frame)
        controls = ttk.Frame(frame)
        controls.pack(side="bottom", fill="x", pady=(9, 0))
        entry_row.pack(side="bottom", fill="x", pady=(10, 0))
        stage = tk.Frame(frame, bg=ui.BG)
        stage.pack(fill="both", expand=True)
        stage.grid_rowconfigure(0, weight=1)
        stage.grid_columnconfigure(1, weight=1)
        self.panels = cockpit.Panels(stage, self.submit_command, lambda: tabs.select(self.log_page))
        self.panels.left.grid(row=0, column=0, sticky="ns", padx=(0,8))
        self.panels.right.grid(row=0, column=2, sticky="ns", padx=(8,0))
        body = tk.Frame(stage, bg=ui.PANEL, highlightthickness=1, highlightbackground=ui.EDGE)
        body.grid(row=0, column=1, sticky="nsew")
        def fit(event):
            if event.widget is not stage:
                return
            for panel, limit in ((self.panels.right, 1020), (self.panels.left, 820)):
                if event.width >= limit:
                    panel.grid()
                else:
                    panel.grid_remove()
        stage.bind("<Configure>", fit)
        self.compact_core = cockpit.Reactor(body, self.status, compact=True)
        self.journal = scrolledtext.ScrolledText(
            body, wrap="word", font=("Segoe UI", 12), bg=ui.PANEL, fg=ui.INK,
            relief="flat", padx=22, pady=14, height=3, state="disabled",
            selectbackground="#534863", takefocus=False,
        )
        self.journal.vbar.configure(background=ui.FIELD, troughcolor=ui.PANEL, width=10)
        self.journal.pack(fill="both", expand=True)
        for role, color in (("user", ui.MUTED), ("assistant", ui.ACCENT)):
            self.journal.tag_configure(role, foreground=color, spacing1=8,
                                       font=("Segoe UI Semibold", 10))
        self.drafting = False
        self.timer_cards = {}
        self.suggestions = tk.Frame(body, bg=ui.PANEL)
        self.suggestions.place(x=1, y=1, relwidth=1, relheight=1)
        self.reactor = cockpit.Reactor(self.suggestions, self.status)
        self.reactor.pack(fill="both", expand=True, pady=(5,0))
        tk.Label(self.suggestions, text="Чем займёмся?", bg=ui.PANEL, fg=ui.INK,
                 font=("Segoe UI Semibold", 19)).pack()
        tk.Label(self.suggestions, text="Скажи «Джарвис» или напиши задачу", bg=ui.PANEL,
                 fg=ui.MUTED, font=("Segoe UI", 10)).pack(pady=(1,9))
        row = tk.Frame(self.suggestions, bg=ui.PANEL)
        row.pack(pady=(0,12))
        for title, prompt in (("Таймер для чая", "можешь поставить таймер для чая?"),
                              ("Проверить ПК", "проверь компьютер")):
            ttk.Button(row, text=title, command=lambda p=prompt:
                       self.submit_command(p)).pack(side="left", padx=3)
        self.command_text = tk.StringVar()
        self.entry = ttk.Entry(entry_row, textvariable=self.command_text, font=("Segoe UI", 12))
        self.entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))
        self.entry.bind("<Control-KeyPress>", entry_shortcut)
        self.entry.bind("<Return>", lambda event: self.submit_command())
        self.send_button = ttk.Button(entry_row, text="Отправить", style="Accent.TButton", command=self.submit_command)
        self.send_button.pack(side="right")
        self.start_button = ttk.Button(controls, text="Включить микрофон", command=self.start)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(controls, text="Остановить", command=self.stop,
                                      state="disabled")
        self.stop_button.pack(side="left")
        self.tray_button = ttk.Button(controls, text="В трей", command=self.hide)
        self.tray_button.pack(side="right")
        self.quick = ttk.Frame(frame)
        self.entry_row = entry_row
        self.quick_deadline = 0.0
        self.entry.focus_set()
        root.bind("<Escape>", lambda _: self.stop() if self.busy and not self.stopping else None)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.poll)
        self.tick()

    def refresh_log(self):
        try:
            rows = workspace_tasks.history()
        except (OSError, workspace_tasks.sqlite3.Error):
            rows = []
        self.log_view.configure(state="normal")
        self.log_view.delete("1.0", "end")
        for stamp, kind, detail in rows:
            self.log_view.insert("end", f"{stamp}    {kind}\n{detail}\n\n")
        if not rows:
            self.log_view.insert("end", "Здесь появятся выполненные задачи и ответы Вари.")
        self.log_view.configure(state="disabled")

    def hide(self):
        if self.tray and self.tray.ready:
            self.controller.background.set()
            self.root.withdraw()
        else:
            self.status.set("Значок в трее пока недоступен")

    def restore(self):
        self.controller.background.clear()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.entry.focus_set()

    def make_settings(self, tabs: ttk.Notebook) -> None:
        """Добавить сохраняемые настройки голоса и обращения по имени."""
        page = ttk.Frame(tabs)
        tabs.add(page, text="Настройки")
        canvas = tk.Canvas(page, bg=ui.BG, highlightthickness=0)
        bar = ttk.Scrollbar(page, command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        panel = ttk.Frame(canvas, padding=(20, 10))
        item = canvas.create_window(0, 0, anchor="nw", window=panel)
        panel.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
        options = settings.snapshot()
        ttk.Label(panel, text="Управление", style="Title.TLabel").pack(anchor="w")
        self.mode_var = tk.StringVar(value=options.get("mode", "Помощник"))
        ttk.Combobox(panel, textvariable=self.mode_var, state="readonly",
                     values=missions.MODES, width=24).pack(anchor="w", pady=5)
        ttk.Label(panel, text="Наблюдатель читает состояние ПК. Помощник согласует рабочий сценарий. "
                  "Автономный открывает рабочие программы по одной просьбе. Расширенный также создаёт проекты.",
                  wraplength=580).pack(anchor="w", pady=5)
        ttk.Label(panel, text="Программы для начала работы").pack(anchor="w", pady=(8,2))
        apps_row = ttk.Frame(panel)
        apps_row.pack(anchor="w")
        self.work_vars = {}
        for name in config.APP_WHITELIST:
            var = tk.BooleanVar(value=name in options.get("work_apps", ["блокнот"]))
            self.work_vars[name] = var
            ttk.Checkbutton(apps_row, text=name, variable=var).pack(side="left")
        self.project_var = tk.StringVar(value=options.get("project", ""))
        project_row = ttk.Frame(panel)
        project_row.pack(fill="x", pady=6)
        def choose_project():
            selected = filedialog.askdirectory(parent=self.root, title="Папка проекта Python")
            if selected:
                self.project_var.set(selected)
        ttk.Button(project_row, text="Папка проекта…", command=choose_project).pack(side="left")
        ttk.Button(project_row, text="Забыть папку", command=lambda:self.project_var.set("")).pack(side="right")
        ttk.Label(panel, textvariable=self.project_var, wraplength=580).pack(anchor="w")
        ttk.Button(panel, text="Сохранить профиль", command=self.save_settings).pack(anchor="w", pady=(4,14))
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
        self.voice_ids = {"Автоматически": "", **speech.windows_voices()}
        self.local_voice = tk.StringVar(value=next(
            (name for name, key in self.voice_ids.items() if key == options.get("sapi_voice")),
            "Автоматически"))
        voice_row = ttk.Frame(panel)
        voice_row.pack(fill="x", pady=4)
        ttk.Label(voice_row, text="Голос Windows").pack(side="left")
        ttk.Combobox(voice_row, state="readonly", textvariable=self.local_voice,
                     values=list(self.voice_ids), width=34).pack(side="right")
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
        self.installed = tk.BooleanVar(value=options.get("installed_apps", False))
        ttk.Label(panel, text="Разрешённые программы", font=("Segoe UI Semibold", 12)).pack(anchor="w")
        ttk.Label(panel, text=", ".join(config.APP_WHITELIST), style="Quiet.TLabel").pack(anchor="w")
        ttk.Checkbutton(panel, text="Также разрешить приложения из меню «Пуск» по точному имени",
                        variable=self.installed).pack(anchor="w", pady=3)
        self.wake_word = tk.BooleanVar(value=options["wake_word"])
        ttk.Checkbutton(
            panel, text="Выполнять команды с обращения «Варя» или «Джарвис»",
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
            if self.busy and self.mode_var.get() != missions.mode():
                self.stop()
            settings.save({
                "mode": self.mode_var.get(), "project": self.project_var.get(),
                "work_apps": [name for name, var in self.work_vars.items() if var.get()],
                "voice": self.voice.get(), "rate": self.rate.get(),
                "volume": self.volume.get(), "wake_word": self.wake_word.get(),
                "installed_apps": self.installed.get(), "sapi_voice": self.voice_ids[self.local_voice.get()],
            })
        except (OSError, ValueError, tk.TclError) as error:
            messagebox.showerror("Настройки", str(error), parent=self.root)
            return False
        logger.info("Настройки сохранены")
        return True

    def submit_command(self, suggestion: str | None = None, *, version: int | None = None) -> None:
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
        self.clear_choices()
        item = ("typed", text) if version is None else ("choice", (version, text))
        self.controller.commands.put(item)
        if suggestion is None:
            self.command_text.set("")
        self.entry.focus_set()

    def clear_choices(self) -> None:
        """Убрать ответы на завершённый вопрос."""
        self.quick_deadline = 0.0
        self.quick.pack_forget()
        for button in self.quick.winfo_children():
            button.destroy()

    def show_choices(self, version: int, deadline: float, choices: tuple[str, ...]) -> None:
        """Предложить короткие ответы именно на текущий вопрос Вари."""
        self.clear_choices()
        if self.stopping or time.monotonic() >= deadline:
            return
        self.quick_deadline = deadline
        self.quick.pack(side="bottom", fill="x", after=self.entry_row, pady=(4, 0))
        for choice in choices:
            ttk.Button(self.quick, text=choice, width=3 if choice.isdigit() else 0,
                       command=lambda value=choice:
                       self.submit_command(value, version=version)).pack(side="left", padx=2)

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
        self.clear_choices()
        self.controller.stop()
        self.stop_button.configure(state="disabled")
        self.status.set("Останавливаюсь… ожидаю запись или сеть")

    def close(self) -> None:
        """Закрыть окно после безопасного завершения рабочего потока."""
        self.closing = True
        self.panels.closed.set()
        if self.tray:
            self.tray.close()
        self.start_button.configure(state="disabled")
        self.stop()
        self.controller.close()

    def tick(self) -> None:
        """Обновлять отсчёт и показывать сигнал даже при занятом микрофоне."""
        if self.closing:
            return
        self.panels.refresh()
        names = features.expired_names()
        if names:
            message = "Время вышло. " + ", ".join(names) + "."
            self.events.put(("timer", message))
            self.controller.alarms.put(message)
            self.organizer.feedback.set(message)
        self.refresh_cards()
        if self.quick_deadline and time.monotonic() >= self.quick_deadline:
            self.clear_choices()
        if self.organizer.winfo_ismapped():
            self.organizer.refresh()
        self.root.after(250, self.tick)

    def card(self, kind: str, value: str) -> None:
        """Встроить карточку результата непосредственно в ленту диалога."""
        view = self.journal
        frame = tk.Frame(view, bg=ui.FIELD, padx=15, pady=10, highlightthickness=1,
                         highlightbackground=ui.EDGE)
        title = {"timer_card": "ТАЙМЕР", "note_card": "ЗАМЕТКА", "screenshot_card": "СНИМОК ЭКРАНА",
                 "artifact_card": "РЕЗУЛЬТАТ ЗАДАЧИ"}[kind]
        tk.Label(frame, text=title, bg=ui.FIELD, fg=ui.ACCENT,
                 font=("Segoe UI Semibold", 9)).pack(anchor="w")
        if kind == "timer_card":
            row = tk.Frame(frame, bg=ui.FIELD)
            row.pack(fill="x")
            tk.Label(row, text=value, bg=ui.FIELD, fg=ui.INK,
                     font=("Segoe UI Semibold", 15)).pack(side="left")
            count = tk.Label(row, bg=ui.FIELD, fg=ui.INK, font=("Consolas", 15))
            count.pack(side="left", padx=16)
            ttk.Button(row, text="Отменить", command=lambda: self.cancel_card(value)).pack(side="left")
            self.timer_cards[value] = (frame, count)
        elif kind == "artifact_card":
            path = Path(value)
            tk.Label(frame, text=path.name, bg=ui.FIELD, fg=ui.INK).pack(anchor="w")
            def open_result():
                try:
                    dest = path.resolve(strict=True)
                    if dest.suffix.lower() in {".html", ".txt", ".zip"} and dest.is_relative_to(settings.DATA_DIR.resolve()):
                        os.startfile(dest)
                except OSError:
                    self.append_turn("assistant", "Файл результата уже недоступен.")
            ttk.Button(frame, text="Открыть результат", command=open_result).pack(anchor="w", pady=4)
        elif kind == "note_card":
            tk.Label(frame, text=value, wraplength=370, justify="left", bg=ui.FIELD,
                     fg=ui.INK, font=("Segoe UI", 11)).pack(anchor="w")
        else:
            from PIL import Image, ImageTk

            path = Path(value)
            try:
                with Image.open(path) as picture:
                    picture.thumbnail((240, 125))
                    preview = ImageTk.PhotoImage(picture.copy())
                label = tk.Label(frame, image=preview, bg=ui.FIELD)
                label.image = preview
                label.pack(pady=4)
            except OSError:
                tk.Label(frame, text=path.name, bg=ui.FIELD, fg=ui.INK).pack()
            ttk.Button(frame, text="Открыть папку", command=lambda: self.open_folder(path)).pack(anchor="w")
        view.configure(state="normal")
        view.window_create("end", window=frame, pady=5)
        view.insert("end", "\n")
        view.configure(state="disabled")
        self.refresh_cards()
        view.see("end")

    def cancel_card(self, name: str) -> None:
        """Отменить только таймер выбранной карточки и обновить обе вкладки."""
        if features.cancel_timer(name):
            self.append_turn("assistant", f"Таймер «{name}» отменён.")
        self.refresh_cards()
        self.organizer.refresh()

    def open_folder(self, path: Path) -> None:
        """Открыть папку сохранённого снимка."""
        try:
            os.startfile(path.parent)
        except OSError:
            self.append_turn("assistant", f"Открой папку вручную: {path.parent}")

    def refresh_cards(self) -> None:
        """Читать оставшееся время из того же списка, что и «Мои дела»."""
        active = dict(features.remaining())
        for name, (frame, count) in tuple(self.timer_cards.items()):
            if not frame.winfo_exists() or name not in active:
                if frame.winfo_exists():
                    self.journal.configure(state="normal")
                    self.journal.delete(str(frame))
                    self.journal.configure(state="disabled")
                del self.timer_cards[name]
            else:
                minutes, seconds = divmod(active[name], 60)
                count.configure(text=f"{minutes:02}:{seconds:02}")

    def append_turn(self, role: str, text: str) -> None:
        """Показать текст модели по мере генерации и заменить черновик полным ответом."""
        if role == "user":
            self.clear_choices()
            self.suggestions.place_forget()
            if not self.compact_core.winfo_manager():
                self.compact_core.pack(side="top", fill="x", before=self.journal.frame)
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
                elif kind == "private_text":
                    self.append_turn("assistant", text)
                elif kind == "level":
                    self.reactor.level = self.compact_core.level = float(text)
                elif kind == "mission":
                    self.panels.last.config(text=text[:130])
                    self.status.set(text[:48])
                    if self.log_page.winfo_ismapped():
                        self.refresh_log()
                elif kind in {"restore", "wake"}:
                    if kind == "restore" or self.root.state() == "withdrawn":
                        self.restore()
                elif kind == "tray_status":
                    self.status.set(text)
                elif kind == "choices":
                    self.show_choices(*text)
                elif kind in {"note_card", "timer_card", "screenshot_card", "artifact_card"}:
                    self.card(kind, text)
                elif kind == "mic_paused" and not self.closing:
                    self.listening = False
                    self.busy = self.sending
                    if not self.busy:
                        self.start_button.configure(state="normal")
                        self.stop_button.configure(state="disabled")
                    self.status.set(text)
                    self.entry.focus_set()
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
    window = VaryaWindow(root)
    window.tray = Tray(window.events)
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    run()
