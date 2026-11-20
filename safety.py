"""Одноразовое голосовое подтверждение строго заданных действий."""

import logging

import config
import speech

logger = logging.getLogger(__name__)
_CONFIRMATIONS = {
    "shutdown": (
        "да выключение",
        "Выключить компьютер через десять секунд? Скажите: да, выключение.",
    ),
    "reboot": (
        "да перезагрузку",
        "Перезагрузить компьютер через десять секунд? "
        "Скажите: да, перезагрузку.",
    ),
}


def normalize_text(text: str) -> str:
    """Нормализовать регистр, пробелы, ё и пунктуацию распознавателя."""
    text = text.lower().replace("ё", "е")
    for mark in ",.!?":
        text = text.replace(mark, " ")
    return " ".join(text.split())


def confirm(action: str) -> bool:
    """Принять следующую точную реплику; при сбое отменить действие."""
    if action not in _CONFIRMATIONS:
        logger.warning("Отклонено неизвестное опасное действие: %s", action)
        return False
    expected, prompt = _CONFIRMATIONS[action]
    if not speech.speak(prompt):
        logger.warning("Подтверждение отменено: приглашение не озвучено")
        return False
    try:
        answer = speech.listen(
            timeout=config.CONFIRM_TIMEOUT,
            phrase_time_limit=config.CONFIRM_PHRASE_TIME_LIMIT,
        )
    except Exception:
        logger.exception("Ошибка получения подтверждения")
        return False
    approved = answer is not None and normalize_text(answer) == expected
    logger.info("Подтверждение %s: %s", action, approved)
    if not approved:
        speech.speak("Действие отменено.")
    return approved
