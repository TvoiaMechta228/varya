"""Исполнение контекстных команд и подтверждённые результаты для интерфейса."""

from dataclasses import dataclass
import re

import actions
import config
import desktop
from dialogue import Dialogue
import features
from safety import normalize_text


@dataclass(frozen=True)
class Result:
    """Ответ и необязательное содержимое карточки."""
    text: str
    card: str = ""
    value: str = ""
    ok: bool = True


def timer_reference(session: Dialogue, verb: str, name: str = "") -> Result:
    """Разрешить ссылку на действующий таймер или запросить выбор."""
    live = dict(features.remaining())
    if not live:
        return Result("Активных таймеров нет.")
    selected = name or session.selected_timer
    if not selected and len(live) == 1:
        selected = next(iter(live))
    if not selected:
        session.choice_verb = verb
        _, prompt = session.ask("выбор таймера", "Какой таймер? " + ", ".join(live) + ".")
        return Result(prompt)
    target = next((label for label in live if normalize_text(label) == normalize_text(selected)), "")
    if not target:
        return Result("Этот таймер уже не активен. Скажи «мои таймеры».")
    session.selected_timer = target
    if verb == "cancel":
        if not features.cancel_timer(target):
            return Result("Таймер уже завершён.")
        session.selected_timer = ""
        return Result(f"Таймер «{target}» отменён.")
    seconds = live[target]
    return Result(f"«{target}» — осталось {features.duration_words(seconds)}.")


def window_action(session: Dialogue, verb: str, name: str) -> Result:
    """Выбрать живое окно и сохранить его идентичность между репликами."""
    live = desktop.windows()
    if name in {"то окно", "это окно", "его"} and session.selected_window:
        matches = [w for w in live if w == session.selected_window]
    else:
        matches = desktop.match_windows(live, "" if name in {"то окно", "это окно", "его"} else name)
    if len(matches) > 1:
        session.window_choices = tuple(matches)
        session.choice_verb = verb
        _, question = session.ask("выбор окна", "Какое окно? " + ". ".join(w[2] for w in matches[:5]))
        return Result(question)
    if not matches:
        return Result("Окно недоступно. Скажи «список окон».")
    return apply_window(session, verb, matches[0])


def apply_window(session: Dialogue, verb: str, target: tuple[int, int, str]) -> Result:
    """Передать исполнителю выбранный дескриптор, процесс и заголовок."""
    if target not in desktop.windows():
        return Result("Окно изменилось. Выбери его ещё раз.")
    answer = desktop.apply_window(verb, target)
    session.selected_window = target
    return Result(answer)


def execute(session: Dialogue, command: str, raw: str) -> Result | None:
    """Выполнить одну распознанную просьбу и вернуть результат после действия."""
    if re.match(r"^не (?:надо|нужно|открывай|запускай|закрывай|выключай|ставь|записывай|делай|нажимай|сохраняй|ищи|отменяй)\b", command):
        return Result("Хорошо, ничего не выполняю.")
    if match := re.fullmatch(r"как (?:мне )?(?:открыть|запустить) (.+)", command):
        return Result(f"Скажи «открой {match[1]}» или введи название в меню «Пуск».")
    if command.startswith("выбор окна "):
        name = command.removeprefix("выбор окна ")
        choices = desktop.match_windows(session.window_choices, name)
        if len(choices) != 1 or session.choice_verb not in desktop.WINDOW_VERBS:
            return Result("Уточни полное название окна и повтори команду.")
        session.window_choices = ()
        return apply_window(session, session.choice_verb, choices[0])
    if match := re.fullmatch(r"(закрой|разверни|сверни|переключись на) (?:окно (.+)|(то окно|это окно|его))", command):
        return window_action(session, match[1], match[2] or match[3])
    if command.startswith("выбор таймера ") and session.choice_verb in {"cancel", "remaining"}:
        return timer_reference(session, session.choice_verb, command.removeprefix("выбор таймера "))
    if command in {"отмени его", "отмени этот таймер", "сколько по нему осталось",
                   "сколько осталось на таймере", "как я назвал таймер"}:
        return timer_reference(session, "cancel" if command.startswith("отмени") else "remaining")
    if match := re.fullmatch(r"таймер(?: (.+?))? на (.+)", raw, re.I):
        seconds = features.duration(normalize_text(match[2]))
        if seconds is None:
            return Result("Укажи время от одной секунды до суток.")
        try:
            label = features.add_timer(match[1] or "", seconds)
        except ValueError as error:
            return Result(str(error))
        session.selected_timer = label
        return Result(f"Таймер «{label}» запущен.", "timer_card", label)
    if command.startswith("запомни "):
        note = raw.split(maxsplit=1)[1]
        features.remember(note)
        session.selected_note = note
        return Result("Записала.", "note_card", note)
    if command in {"прочитай эту заметку", "прочти эту заметку", "покажи эту заметку"}:
        notes = [n["text"] for n in features.read_notes()]
        if session.selected_note in notes:
            return Result(session.selected_note)
        if len(notes) == 1:
            session.selected_note = notes[0]
            return Result(notes[0])
        if notes:
            _, question = session.ask("выбери заметку", "Какую заметку прочитать? Назови часть текста.")
            return Result(question)
        return Result("Заметок пока нет.")
    if command.startswith("выбери заметку "):
        key = command.removeprefix("выбери заметку ")
        notes = {n["text"] for n in features.read_notes() if key in normalize_text(n["text"])}
        if len(notes) == 1:
            session.selected_note = notes.pop()
            return Result(session.selected_note)
        return Result(session.ask("выбери заметку", "Уточни фрагмент текста заметки.")[1])
    if command == "скриншот":
        path = actions.take_screenshot()
        return Result("Снимок экрана сохранён.", "screenshot_card", str(path))
    return None
