"""Смысл одной пользовательской реплики: типизированное предложение локальной Ollama."""

import asyncio
from dataclasses import dataclass
import json
import re

import aiohttp

import conversation
import speech


# Модель не выдаёт системных команд, путей, сочетаний клавиш или цепочек действий.
ACTIONS = {
    "chat": "общение, объяснение, вопрос как сделать что-либо самому",
    "unsupported": "явная просьба выполнить отсутствующее или составное действие",
    "timer": "напоминание через интервал; seconds — интервал в секундах, argument — о чём напомнить",
    "timers": "перечислить действующие таймеры",
    "remaining": "сколько осталось у выбранного таймера",
    "note": "записать заметку; argument — только продиктованный текст",
    "notes": "прочитать сохранённые заметки",
    "app": "открыть программу; argument — только название, напр. калькулятор",
    "folder": "показать одну из папок: загрузки, документы, изображения, рабочий стол, музыка, видео",
    "settings": "открыть раздел Windows: звук, экран, bluetooth, сеть, приложения",
    "windows": "перечислить открытые окна",
    "focus": "перейти к открытому окну; argument — часть названия",
    "minimize": "свернуть конкретное окно; argument — название окна",
    "maximize": "развернуть конкретное окно; argument — название окна",
    "buttons": "перечислить кнопки в окне; argument — название окна, пустая строка для активного",
    "quieter": "уменьшить системную громкость",
    "louder": "увеличить системную громкость",
    "search": "найти в интернете; argument — поисковый запрос",
    "screenshot": "сохранить снимок экрана",
}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": list(ACTIONS)},
        "argument": {"type": "string"},
        "seconds": {"type": "integer", "minimum": 0, "maximum": 86400},
    },
    "required": ["action", "argument", "seconds"],
}
INSTRUCTIONS = (
    "Ты разбираешь русский разговорный запрос для Windows. Выбери ровно одно намерение. "
    "Понимай просьбы по смыслу, включая косвенные. Не выполняй действий. "
    "Для вопроса, отрицания, обсуждения или цитаты выбери chat. Не угадывай пропущенное время. "
    "Для неподдерживаемой операции или нескольких действий выбери unsupported. "
    "Не превращай просьбу о помощи в автоматическое действие. "
    "Неиспользованные поля: пустая строка и 0. "
    "Пример: «через четверть часа напомни вынуть пирог» => timer, «вынуть пирог», 900. "
    "«Где лежит то, что скачалось?» => folder, «загрузки», 0. "
    "«Слишком орёт, убавь чуток» => quieter. "
    + json.dumps(ACTIONS, ensure_ascii=False)
)


@dataclass(frozen=True)
class Intent:
    """Проверенное предложение, которое ещё ничего не выполнило."""
    action: str
    argument: str = ""
    seconds: int = 0

    @classmethod
    def parse(cls, text: str) -> "Intent":
        value = json.loads(text)
        if not isinstance(value, dict) or set(value) != {"action", "argument", "seconds"}:
            raise ValueError("Некорректный формат намерения")
        action, arg, seconds = (value[k] for k in ("action", "argument", "seconds"))
        if not isinstance(action, str) or action not in ACTIONS or not isinstance(arg, str):
            raise ValueError("Неизвестное действие")
        if len(arg) > 500 or (arg and not arg.isprintable()):
            raise ValueError("Некорректный аргумент")
        if type(seconds) is not int or not 0 <= seconds <= 86400 or (action != "timer" and seconds):
            raise ValueError("Некорректная длительность")
        if action in {"chat", "unsupported", "timers", "notes", "windows", "quieter", "louder", "screenshot"}:
            arg = ""
        return cls(action, arg.strip(), seconds)


def ordinary_question(text: str) -> bool:
    """Сразу направить очевидный вопрос или просьбу пояснить в разговор."""
    return bool(re.match(
        r"^(?:почему|зачем|объясни|расскажи|что такое|как (?:мне|работает|открыть|сделать)|"
        r"не (?:хочу|стоит|нужно|надо)|о ч[её]м поговорим|проще|поподробнее|перефразируй|привет|спасибо)\b", text, re.I))


async def interpret(text: str, last_result: str = "") -> Intent:
    """Получить и проверить JSON с возможностью прервать холодный запуск модели."""
    payload = {"model": conversation.MODEL, "stream": False, "format": SCHEMA,
               "messages": [{"role": "system", "content": INSTRUCTIONS},
                            {"role": "user", "content": json.dumps(
                                {"request": text, "previous_result": last_result[:240]},
                                ensure_ascii=False)}],
               "options": {"temperature": 0, "num_predict": 160, "num_ctx": 4096}}

    async def request() -> Intent:
        timeout = aiohttp.ClientTimeout(total=120, connect=3)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
            async with client.post(conversation.URL, json=payload, allow_redirects=False) as response:
                response.raise_for_status()
                body = await response.json()
                return Intent.parse(body["message"]["content"])

    task = asyncio.create_task(request())
    try:
        while not task.done():
            if speech.is_cancelled():
                raise InterruptedError
            await asyncio.wait({task}, timeout=.12)
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
