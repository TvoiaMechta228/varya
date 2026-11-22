"""Формулировки команд и краткие уточнения в пределах одной сессии."""

from dataclasses import dataclass
import re
import time

from safety import normalize_text


# Переписываем только глагол запроса. Диктовка, поисковые символы и имена
# остаются в том виде, в котором их ввёл пользователь.
_PHRASES = (
    (r"(?:поищи(?:\s+в\s+(?:интернете|яндексе))?|найди\s+в\s+(?:интернете|яндексе))", "найди в интернете"),
    (r"(?:запиши заметку|сохрани заметку|добавь заметку)", "запомни"),
    (r"(?:заведи|включи|поставь) таймер", "таймер"),
    (r"(?:сделай снимок экрана|сделай скриншот)", "скриншот"),
    (r"(?:подскажи|скажи)[, ]+(?:который час|сколько времени)", "который час"),
)
_RULES = [(re.compile(r"^" + pattern + r"(?=$|[\s,:.!?])", re.I), value)
          for pattern, value in _PHRASES]


def phrase(text: str) -> str:
    """Выделить прямую просьбу и сохранить аргументы, включая пунктуацию."""
    text = re.sub(r"^пожалуйста[, ]+", "", text.strip(), flags=re.I)
    for pattern, replacement in _RULES:
        match = pattern.match(text)
        if match:
            tail = text[match.end():].lstrip(" ,:")
            return f"{replacement} {tail}".rstrip()
    return text


_QUESTIONS = {
    "таймер": ("таймер на", "На сколько поставить таймер?"),
    "найди в интернете": ("найди в интернете", "Что поискать?"),
    "запомни": ("запомни", "Что записать в заметку?"),
}


@dataclass
class Dialogue:
    """Хранить один ожидаемый аргумент. Подтверждения питания обрабатываются отдельно."""

    pending: str = ""
    until: float = 0.0

    def prepare(self, text: str) -> tuple[str, str]:
        """Вернуть готовую команду либо вопрос, который нужно показать пользователю."""
        key = normalize_text(text)
        if self.pending and time.monotonic() < self.until:
            prefix, self.pending = self.pending, ""
            if key in {"отмена", "отмени", "передумал", "передумала"}:
                return "", "Отменила."
            if prefix == "таймер на":
                text = re.sub(r"^на\s+", "", text, flags=re.I)
            return f"{prefix} {text}", ""
        self.pending = ""
        text = phrase(text)
        question = _QUESTIONS.get(normalize_text(text))
        if question:
            self.pending, prompt = question
            self.until = time.monotonic() + 60
            return "", prompt
        return text, ""
