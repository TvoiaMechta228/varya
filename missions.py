"""Планирование и исполнение задач командного центра."""

import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import actions
import config
import features
import maintenance
import project_tools
import sequences
import services
import settings
import speech
import telemetry
import workspace_tasks as files
from command_results import Result

MODES = ("Наблюдатель", "Помощник", "Автономный", "Расширенный")
LABELS = {
    "ocr": "Текст с экрана",
    "ocr_window": "Текст выбранного окна",
    "inbox": "Входящие Outlook",
    "draft": "Черновик письма",
    "archive": "Архив исходников",
    "pytest": "Тесты проекта",
    "sequence": "Последовательность",
    "diagnose": "Диагностика ПК",
    "sort": "Сортировка стола",
    "undo": "Отмена сортировки",
    "resume": "Продолжение сортировки",
    "work": "Рабочий режим",
    "check": "Проверка проекта",
    "expenses": "Приложение расходов",
    "shop": "Демо-магазин",
    "maintenance": "Обслуживание ПК",
    "cleanup": "Очистка временных файлов",
    "updates": "Проверка обновлений",
    "packages": "Программы WinGet",
    "agenda": "Календарь Outlook",
    "cloud": "Папка OneDrive",
}
PATTERNS = {
    "ocr": r"(?:прочитай|распознай) (?:текст (?:с экрана|на экране)|экран)|что написано на экране",
    "ocr_window": r"прочитай (?:выбранное|это) окно",
    "inbox": r"(?:покажи|прочитай) непрочитанные письма",
    "draft": r"создай (?:черновик письма|новое письмо)",
    "archive": r"(?:заархивируй|упакуй) (?:мой )?проект|создай архив проекта",
    "pytest": r"(?:запусти|прогони) тесты(?: проекта)?",
    "updates": r"(?:проверь|покажи) обновления(?: программ)?",
    "cleanup": r"(?:очисти|убери|удали) (?:старые |ненужные )?временные файлы",
    "maintenance": r"обслуживание (?:пк|компьютера)|проверь компьютер очисти ненужные файлы найди причины торможения обнови программы и подготовь отчет",
    "diagnose": r"(?:проверь|проверить|проведи диагностику|диагностика) (?:моего |мой )?(?:пк|компьютер[а]?)|почему (?:мой )?(?:компьютер|ноутбук|пк) (?:так )?тормозит",
    "sort": r"(?:наведи|навести) порядок на (?:моем )?рабочем столе|(?:разбери|рассортируй|упорядочь) (?:файлы на рабочем столе|рабочий стол)",
    "resume": r"продолжи сортировку(?: стола)?",
    "undo": r"(?:отмени|откати) сортировку(?: стола)?|верни файлы на рабочий стол",
    "work": r"подготовь(?: меня| компьютер| пк)? к работе|начни рабочий день|рабочий режим",
    "check": r"проверь (?:мой )?(?:проект|код)|найди ошибк[уи] в моей программе",
    "expenses": r"создай (?:мне )?приложение для учета расходов",
    "shop": r"создай (?:мне )?(?:интернет[- ]магазин|демо[- ]магазин)",
    "agenda": r"(?:что у меня|какие встречи) (?:сегодня|на сегодня)|(?:покажи )?календарь(?: на сегодня)?",
    "cloud": r"(?:открой|покажи) (?:мое облако|onedrive|ван драйв)",
}
READ_ONLY = {"diagnose", "agenda", "inbox", "check", "ocr", "ocr_window"}


def identify(command: str) -> str | None:
    key = command.removeprefix("пожалуйста ").removesuffix(" пожалуйста")
    package = re.fullmatch(r"(установи|обнови) (7-zip|vlc|powertoys)", key)
    if package:
        return (
            "packages/"
            + ("install" if package[1] == "установи" else "upgrade")
            + "/"
            + package[2]
        )
    for name, pattern in PATTERNS.items():
        if re.fullmatch(pattern, key):
            return name
    if re.fullmatch(
        r"(?:проверь|продиагностируй) (?:компьютер|пк) и (?:подготовь|составь|сохрани) отчет",
        key,
    ):
        return "diagnose"
    return None


@dataclass(frozen=True)
class Plan:
    kind: str
    expires: float
    mode: str
    batch: files.Batch | None = None
    programs: tuple[str, ...] = ()
    candidates: tuple = ()
    steps: tuple[sequences.Step, ...] = ()
    window: tuple[int, int, str] | None = None


def progress(detail: str) -> None:
    speech.publish("mission", detail)
    logging.getLogger(__name__).info("Шаг: %s", detail)


def audit(kind: str, detail: str) -> None:
    try:
        files.record(kind, detail)
    except (OSError, sqlite3.Error):
        logging.getLogger(__name__).exception("Не удалось сохранить журнал")


def mode() -> str:
    return settings.snapshot().get("mode", "Помощник")


def save_report(name: str, report: str) -> Path:
    directory = settings.DATA_DIR / "reports"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.txt"
    with path.open("x", encoding="utf-8-sig") as f:
        f.write(report)
    return path


def request(session, kind: str) -> Result:
    current = mode()
    parts = kind.split("/")
    kind = parts[0]
    if current == "Наблюдатель" and kind not in READ_ONLY:
        return Result(
            "Сейчас включён наблюдатель. Выбери режим помощника в настройках для действий с ПК."
        )
    if kind in {"packages", "expenses", "shop", "pytest"} and current != "Расширенный":
        return Result("Для этой операции выбери «Расширенный» в настройках.")
    plan = Plan(kind, time.monotonic() + 90, current)
    if kind == "ocr_window":
        if not session.selected_window:
            return Result(
                "Выбери окно командой «переключись на окно …», затем скажи «прочитай выбранное окно»."
            )
        return run(Plan(kind, plan.expires, current, window=session.selected_window))
    if kind in {"archive", "pytest"}:
        project = settings.snapshot().get("project")
        if not project or not Path(project).is_dir():
            return Result("Сначала выбери папку проекта в настройках.")
        plan = Plan(kind, plan.expires, current, programs=(project,))
        question = (
            f"Запустить pytest в «{project}»? Код тестов выполнится с твоими правами."
            if kind == "pytest"
            else f"Собрать исходники из «{project}» в локальный ZIP?"
        )
    elif kind == "packages":
        action, package = parts[1:]
        plan = Plan(kind, plan.expires, current, programs=(action, package))
        verb = "Установить" if action == "install" else "Обновить"
        question = f"{verb} {maintenance.PACKAGES[package]} через WinGet для текущего пользователя?"
    elif kind == "cleanup":
        candidates = maintenance.temp_candidates()
        if not candidates:
            return Result(
                "В пользовательской папке Temp нет обычных файлов .tmp старше семи дней."
            )
        plan = Plan(kind, plan.expires, current, candidates=candidates)
        speech.publish("assistant", "\n".join(name for name, _ in candidates))
        question = f"Переместить показанные временные файлы в корзину Windows? Файлов — {len(candidates)}."
    elif kind in {"sort", "resume", "undo"}:
        progress("Проверяю состав рабочего стола")
        batch = files.preview() if kind == "sort" else files.latest()
        if not batch or not batch.moves:
            return Result(
                "Подходящих файлов нет."
                if kind == "sort"
                else "Сохранённого плана сортировки нет."
            )
        plan = Plan(kind, time.monotonic() + 90, current, batch)
        listing = "\n".join(
            f"{m.name}  →  {files.SORT_DIR}/{m.group}/" for m in batch.moves
        )
        if kind == "undo":
            listing = "\n".join(
                f"{files.SORT_DIR}/{m.group}/{m.name}  →  Рабочий стол"
                for m in batch.moves
            )
        speech.publish("assistant", listing)
        question = f"В плане файлов — {len(batch.moves)}. " + (
            "Вернуть их на прежнее место?"
            if kind == "undo"
            else "Разложить их по показанным папкам?"
        )
    elif kind == "work":
        apps = tuple(settings.snapshot().get("work_apps", ["блокнот"]))
        plan = Plan(kind, plan.expires, current, programs=apps)
        question = (
            "Открыть " + ", ".join(apps) + " и проверить таймеры?"
            if apps
            else "Проверить таймеры?"
        )
        if current in {"Автономный", "Расширенный"}:
            return run(plan)
    elif kind in {"expenses", "shop"}:
        question = "Создать локальный проект и проверить готовую страницу?"
    else:
        return run(plan)
    session.mission = plan
    audit(LABELS[kind], "Предложен план. Требуется подтверждение.")
    return Result(question + " Да или нет?")


def sequence_request(session, text: str) -> Result | None:
    try:
        steps = sequences.parse(text)
    except ValueError as error:
        return Result(str(error))
    if not steps:
        return None
    session.mission = Plan("sequence", time.monotonic() + 90, mode(), steps=steps)
    lines = [f"{i}. {step.title}" for i, step in enumerate(steps, 1)]
    speech.publish("assistant", "\n".join(lines))
    audit("Последовательность", "\n".join(lines))
    return Result(f"Выполнить {len(steps)} шага по порядку? Да или нет?")


def confirm(session, command: str) -> Result | None:
    plan, session.mission = session.mission, None
    if plan is None:
        return None
    if command in {"нет", "отмена", "не надо", "отмени"}:
        audit(LABELS[plan.kind], "Отменено пользователем.")
        return Result("Отменила план.")
    if command not in {"да", "подтверждаю", "сделай"}:
        return None
    if time.monotonic() >= plan.expires or plan.mode != mode() or speech.is_cancelled():
        return Result("План больше не действует. Запроси его снова.")
    return run(plan)


def run(plan: Plan) -> Result:
    name = LABELS[plan.kind]
    audit(name, "Начато выполнение.")
    try:
        if speech.is_cancelled() or plan.mode != mode():
            raise InterruptedError
        result = execute(plan)
    except InterruptedError:
        result = Result("Выполнение остановлено.")
    except Exception as error:
        logging.getLogger(__name__).exception("Не удалось завершить %s", name)
        result = Result(f"Задача «{name}» остановлена. {error}")
    audit(name, result.text + (f"\n{result.value}" if result.value else ""))
    progress(result.text)
    return result


def execute(plan: Plan) -> Result:
    stopped = lambda: speech.is_cancelled() or mode() != plan.mode
    if plan.kind in {"ocr", "ocr_window"}:
        import screen_reader

        progress("Распознаю текст на компьютере")
        words = screen_reader.read(plan.window)
        if stopped():
            raise InterruptedError
        if not words:
            return Result("Windows не распознала текст на этом изображении.")
        speech.publish("private_text", words)
        return Result("Распознанный текст готов.")
    if plan.kind == "draft":
        os.startfile("mailto:")
        return Result(
            "Открыла новое письмо в почтовом приложении. Отправку выполняешь ты."
        )
    if plan.kind == "inbox":
        speech.publish("private_text", services.inbox())
        return Result("Проверила доступ к входящим Outlook.")
    if plan.kind in {"archive", "pytest"}:
        root = Path(plan.programs[0])
        if (
            str(root) != str(Path(settings.snapshot().get("project", "")))
            or not root.is_dir()
        ):
            return Result("Папка проекта изменилась. Запроси план снова.")
        if plan.kind == "archive":
            progress("Собираю исходники и проверяю ZIP")
            return Result(
                "Архив готов.",
                "artifact_card",
                str(project_tools.archive(root, stopped)),
            )
        progress("Выполняю тесты выбранного проекта")
        ok, report = project_tools.test_project(root, stopped)
        return Result(
            "Тесты прошли." if ok else "Тесты завершились с ошибкой. Открой отчёт.",
            "artifact_card",
            str(save_report("pytest", report)),
        )
    if plan.kind == "sequence":
        completed = 0
        for index, step in enumerate(plan.steps, 1):
            if stopped():
                break
            progress(f"{index}/{len(plan.steps)} · {step.title}")
            try:
                sequences.execute(step)
            except Exception as error:
                logging.getLogger(__name__).exception("Не завершён шаг %s", step.title)
                return Result(
                    f"Выполнено {completed} из {len(plan.steps)}. На шаге «{step.title}» возникла ошибка: {error}"
                )
            audit("Шаг выполнен", step.title)
            completed += 1
        return Result(f"Выполнено шагов — {completed} из {len(plan.steps)}.")
    if plan.kind == "cleanup":
        progress("Проверяю файлы перед перемещением в корзину")
        return Result(maintenance.recycle(plan.candidates, stopped))
    if plan.kind in {"updates", "packages"}:
        progress("Запрашиваю WinGet")
        args = plan.programs if plan.kind == "packages" else ("list", "")
        ok, output = maintenance.winget(*args, stopped)
        answer = (
            "WinGet завершил операцию."
            if ok
            else "WinGet не смог завершить операцию. Подробности в отчёте."
        )
        return Result(answer, "artifact_card", str(save_report("winget", output)))
    if plan.kind in {"diagnose", "maintenance"}:
        summary, report = telemetry.diagnose(stopped, progress)
        if stopped():
            raise InterruptedError
        if plan.mode == "Наблюдатель":
            speech.publish("assistant", report)
            return Result(summary)
        if plan.kind == "maintenance":
            progress("Ищу старые временные файлы и проверяю обновления")
            candidates = maintenance.temp_candidates()
            ok, updates = maintenance.winget("list", "", stopped)
            if stopped():
                raise InterruptedError
            report += "\n\nВременные файлы для просмотра\n" + "\n".join(
                name for name, _ in candidates
            )
            report += "\n\nПроверка WinGet\n" + updates
            summary += f" Временных файлов для просмотра — {len(candidates)}. "
            summary += (
                "Список обновлений в отчёте." if ok else "WinGet не завершил проверку."
            )
            summary += " Для очистки скажи «очисти временные файлы». Для обновления укажи программу."
        progress("Сохраняю отчёт")
        return Result(summary, "artifact_card", str(save_report("diagnostics", report)))
    if plan.kind == "check":
        project = settings.snapshot().get("project")
        if not project:
            return Result(
                "Выбери папку Python-проекта в настройках и повтори проверку."
            )
        progress("Проверяю синтаксис файлов")
        report = project_tools.check_project(Path(project), stopped)
        if plan.mode == "Наблюдатель":
            speech.publish("assistant", report)
            return Result(report.splitlines()[0])
        return Result(
            report.splitlines()[0], "artifact_card", str(save_report("project", report))
        )
    if plan.batch is not None:
        progress("Сверяю файлы и выполняю план")
        count, skipped = files.apply(plan.batch, plan.kind == "undo", stopped)
        left = len(plan.batch.moves) - count - skipped
        return Result(
            f"Перемещено файлов — {count}. Пропущено — {skipped}. Осталось — {left}."
        )
    if plan.kind == "work":
        notes = []
        for app in plan.programs:
            if stopped():
                notes.append("Остановлено.")
                break
            progress(f"Открываю {app}")
            try:
                ok = app in config.APP_WHITELIST and actions.open_app(app)
            except OSError:
                ok = False
            notes.append(
                f"{app} — " + ("запрошен запуск" if ok else "не удалось открыть")
            )
        notes.append(f"Активных таймеров — {len(features.remaining())}.")
        return Result(". ".join(notes))
    if plan.kind == "agenda":
        progress("Читаю встречи из открытого Outlook")
        speech.publish("private_text", services.agenda())
        return Result("Проверила доступ к календарю Outlook.")
    if plan.kind == "cloud":
        folder = services.cloud_folder()
        if folder is None:
            return Result("Папка OneDrive не найдена. Подключи OneDrive в Windows.")
        os.startfile(folder)
        return Result("Открыла локальную папку OneDrive.")
    progress("Создаю проект и проверяю страницу")
    path = project_tools.create_app(plan.kind)
    return Result(
        "Проект готов. Открой страницу из карточки.", "artifact_card", str(path)
    )
