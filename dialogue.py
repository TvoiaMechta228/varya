"""Разбор прямых просьб и состояние текущего диалога."""

from dataclasses import dataclass
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from missions import Plan
    from requests_pc import Proposal
    from window_buttons import Button


import features
from safety import normalize_text


_PHRASES = (
    (r"(?:поищи(?:\s+в\s+(?:интернете|яндексе))?|найди\s+в\s+(?:интернете|яндексе))", "найди в интернете"),
    (r"(?:запиши|сохрани|добавь) заметку", "запомни"),
    (r"(?:заведи|включи|поставь) таймер", "таймер"),
    (r"(?:сделай снимок экрана|сделай скриншот)", "скриншот"),
    (r"(?:подскажи|скажи)[, ]+(?:который час|сколько времени)", "который час"),
    (r"(?:запусти|включи)(?: приложение)?", "открой"),
)
_RULES = [(re.compile(r"^" + pattern + r"(?=$|[\s,:.!?])", re.I), value)
          for pattern, value in _PHRASES]
_INFINITIVES = {"открыть": "открой", "запустить": "запусти", "поставить": "поставь",
                "записать": "запиши", "поискать": "поищи", "закрыть": "закрой",
                "свернуть": "сверни", "развернуть": "разверни"}


def phrase(text: str) -> str:
    """Нормализовать формулировку просьбы, сохраняя текст заметок и поиска."""
    text = re.sub(r"^пожалуйста[, ]+", "", text.strip(), flags=re.I)
    text = re.sub(r"^(открой|запусти|включи|покажи)\s+(?:мне|нам)\s+", r"\1 ", text, flags=re.I)
    polite = re.fullmatch(r"(?:можешь|можно попросить тебя)\s+(\w+)\s+(.+?)\??", text, re.I)
    if polite and polite[1].lower() in _INFINITIVES:
        text = f"{_INFINITIVES[polite[1].lower()]} {polite[2]}"
    if re.match(r"^(?:открой|запусти|включи|покажи|открыть|запустить)\b", text, re.I):
        text = re.sub(r"[, ]+пожалуйста[.!?]*$", "", text, flags=re.I).rstrip(".!?")
    head, _, tail = text.partition(" ")
    if head.lower() in _INFINITIVES:
        text = _INFINITIVES[head.lower()] + (" " + tail if tail else "")
    text = re.sub(r"^(открой|запусти|поставь),?\s+пожалуйста[, ]+", r"\1 ", text, flags=re.I)
    for pattern, replacement in _RULES:
        match = pattern.match(text)
        if match:
            tail = text[match.end():].lstrip(" ,:")
            return f"{replacement} {tail}".rstrip()
    return text


@dataclass
class Dialogue:
    """Ожидаемый ответ и проверяемые ссылки на объекты текущей сессии."""

    pending: str = ""
    until: float = 0.0
    timer_name: str = ""
    selected_timer: str = ""
    selected_note: str = ""
    selected_window: tuple[int, int, str] | None = None
    window_choices: tuple[tuple[int, int, str], ...] = ()
    choice_verb: str = ""
    mission: "Plan | None" = None
    proposal: "Proposal | None" = None
    button_target: tuple[int, int, str] | None = None
    buttons: tuple["Button", ...] = ()
    capability: object | None = None
    file_choices: tuple[str, ...] = ()
    selected_file: str = ""
    field_choices: tuple = ()
    plan_id: str = ""
    plan_waiting: bool = False
    last_request: str = ""
    last_result: str = ""
    yandex_service: str = ""
    yandex_until: float = 0.0

    def ask(self, slot: str, prompt: str) -> tuple[str, str]:
        """Ожидать один аргумент в течение минуты."""
        self.pending = slot
        self.until = time.monotonic() + 60
        return "", prompt

    def prepare(self, text: str, *, now: float | None = None) -> tuple[str, str]:
        """Вернуть команду или уточнение. Ответ на вопрос остаётся данными."""
        key = normalize_text(text)
        if now is None:
            now = time.monotonic()
        if self.pending and now < self.until:
            slot, self.pending = self.pending, ""
            if key in {"отмена", "отмени", "передумал", "передумала"}:
                return "", "Отменила."
            if slot == "duration":
                period = key.removeprefix("на ")
                if features.duration(period) is None:
                    return self.ask(slot, "На какое время? Например, на полчаса или на пять минут.")
                return f"таймер {self.timer_name} на {period}".replace("  ", " "), ""
            return f"{slot} {text}", ""
        self.pending = ""
        text = phrase(text)
        key = normalize_text(text)
        if key == "таймер" or key.startswith("таймер "):
            name, _, period = (" " + key.removeprefix("таймер").strip()).partition(" на ")
            name = name.strip()
            name = (name or "").removeprefix("для ")
            if " для " in key:
                name = {"чая": "Чай", "кофе": "Кофе", "работы": "Работа"}.get(name, name.capitalize())
            if features.duration(period or "") is None:
                self.timer_name = name
                return self.ask("duration", "На сколько?")
            return f"таймер {name} на {period}".replace("  ", " "), ""
        questions = {"найди в интернете": "Что поискать?", "запомни": "Что записать в заметку?",
                     "открой": "Какое приложение открыть?"}
        if key in questions:
            return self.ask(key, questions[key])
        return text, ""
