"""Последовательности разрешённых действий, составленные из прямой просьбы."""

import re
from dataclasses import dataclass

import actions
import config
import speech
from dialogue import phrase
from safety import normalize_text


@dataclass(frozen=True)
class Step:
    action: str
    argument: str
    title: str


def parse(text: str) -> tuple[Step, ...] | None:
    """Разделить явную последовательность. Продиктованный текст остаётся данными."""
    if re.match(
        r"^(?:запомни|запиши|сохрани заметку|напомни|поищи|найди|напечатай)\b",
        text.strip(),
        re.IGNORECASE,
    ):
        return None
    # В цитатах могут встречаться глаголы, которые являются частью названия.
    if any(mark in text for mark in ('"', '«', '»')):
        return None
    parts = re.split(
        r"(?:\s*[,;]\s*|\s+)(?:а\s+)?(?:затем|потом|после этого|далее)\s+"
        r"|(?:\s*[,;]\s*|\s+(?:и\s+)?)(?=(?:открой|запусти|включи|покажи|"
        r"сделай|выключи|перезагрузи|удали|закрой|найди|запустить|открыть|включить)\b)",
        re.sub(r"^сначала\s+", "", text.strip(), flags=re.I),
        flags=re.IGNORECASE,
    )
    if len(parts) < 2:
        return None
    if len(parts) > 5:
        raise ValueError("В одном плане помещается до пяти шагов.")
    from requests_pc import _ALIASES, FOLDERS, PAGES

    steps = []
    for part in parts:
        raw = phrase(re.sub(r"^сначала\s+", "", part.strip(" ,;.!?"), flags=re.IGNORECASE))
        key = normalize_text(raw)
        open_target = key.removeprefix("открой ").removeprefix("покажи ")
        open_target = config.APP_ALIASES.get(open_target, open_target)
        if open_target in config.WEB_SHORTCUTS:
            step = Step("service", open_target, "Открыть " + open_target)
        elif open_target in config.APP_WHITELIST:
            step = Step("app", open_target, "Открыть " + open_target)
        elif (folder := _ALIASES.get(open_target, open_target)) in FOLDERS:
            step = Step("folder", folder, "Открыть папку «" + folder + "»")
        elif (
            match := re.fullmatch(r"(?:настройки|параметры) (.+)", open_target)
        ) and _ALIASES.get(match[1], match[1]) in PAGES:
            step = Step(
                "settings", _ALIASES.get(match[1], match[1]), "Открыть " + open_target
            )
        elif key == "скриншот":
            step = Step("screenshot", "", "Сохранить снимок экрана")
        elif key in ("тише", "громче"):
            step = Step("volume", key, "Сделать " + key)
        else:
            raise ValueError(
                f"Не могу включить «{part[:90]}» в план. Ни один шаг не выполнен."
            )
        steps.append(step)
    return tuple(steps)


def execute(step: Step) -> None:
    if step.action == "service":
        actions.open_service(step.argument)
    elif step.action == "app":
        if not actions.open_app(step.argument):
            raise OSError("Программа недоступна.")
    elif step.action in {"folder", "settings"}:
        from requests_pc import open_location

        open_location(step.action, step.argument)
    elif step.action == "volume":
        actions.media_control(step.argument)
    elif step.action == "screenshot":
        speech.publish("screenshot_card", str(actions.take_screenshot()))
    else:
        raise ValueError("Неизвестный шаг.")
