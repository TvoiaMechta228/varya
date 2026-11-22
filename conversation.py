"""Диалог с локальной Ollama. История живёт только в текущем процессе."""

import asyncio
from collections import deque
import json
import logging

import aiohttp

import speech

MODEL = "qwen2.5:7b"
URL = "http://127.0.0.1:11434/api/chat"
SYSTEM = (
    "Ты Варя, русскоязычная голосовая помощница. Отвечай естественно, "
    "на ты, в 1–3 предложениях без Markdown. Учитывай предыдущие реплики. "
    "Ты ведёшь разговор. Действия Windows выполняет отдельный обработчик. "
    "Не выдумывай результаты действий, доступ к экрану или к файлам."
)
history: deque[dict[str, str]] = deque(maxlen=12)
enabled = False


async def _request(messages: list[dict[str, str]]) -> str:
    """Дождаться ответа с ограничением времени и поддержкой остановки."""
    payload = {"model": MODEL, "messages": messages, "stream": True,
               "options": {"num_predict": 140, "num_ctx": 4096}}

    async def generate(session: aiohttp.ClientSession) -> str:
        async with session.post(URL, json=payload, allow_redirects=False) as r:
            r.raise_for_status()
            parts = []
            async for line in r.content:
                if not line.strip():
                    continue
                data = json.loads(line)
                if "error" in data:
                    raise ValueError("Модель прервала ответ")
                token = data["message"]["content"]
                if token:
                    parts.append(token)
                    speech.publish("delta", token)
                if data.get("done"):
                    answer = "".join(parts).strip()
                    if answer:
                        return answer
                    break
            raise ValueError("Ответ модели не завершён")

    timeout = aiohttp.ClientTimeout(total=120, connect=4)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        task = asyncio.create_task(generate(session))
        try:
            while not task.done():
                if speech.is_cancelled():
                    return ""
                await asyncio.wait({task}, timeout=0.15)
            return task.result()
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def reply(text: str) -> None:
    """Озвучить ответ и сохранить в памяти только завершённый обмен."""
    if len(text) > 2000:
        speech.speak("Раздели, пожалуйста, вопрос на несколько частей.")
        return
    user = {"role": "user", "content": text}
    try:
        answer = asyncio.run(_request([{"role": "system", "content": SYSTEM},
                                       *history, user]))
    except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError,
            KeyError, TypeError, AttributeError):
        logging.getLogger(__name__).warning("Ошибка локальной Ollama", exc_info=True)
        if not speech.is_cancelled():
            speech.speak("Не получила ответ. Проверь, запущена ли Ollama "
                         "с моделью qwen2.5:7b.")
        return
    finally:
        speech.publish("end_draft", "")
    if answer and not speech.is_cancelled():
        speech.speak(answer)
        if not speech.is_cancelled():
            history.extend((user, {"role": "assistant", "content": answer}))


def control(command: str) -> bool:
    """Переключить режим или очистить память по прямой команде."""
    global enabled
    if command in {"давай поговорим", "включи разговор"}:
        enabled = True
        speech.speak("Я здесь. О чём поговорим?")
    elif command in {"режим команд", "закончи разговор"}:
        enabled = False
        history.clear()
        speech.speak("Вернулась к командам.")
    elif command == "забудь разговор":
        history.clear()
        speech.speak("Начнём с чистого листа.")
    else:
        return False
    return True
