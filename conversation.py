"""Диалог с локальной Ollama. История живёт только в текущем процессе."""

import asyncio
from collections import deque
import json
import logging
import re

import aiohttp
import psutil

import speech

MODEL = "qwen2.5:7b"
URL = "http://127.0.0.1:11434/api/chat"
SYSTEM = (
    "Ты Варя, голосовая помощница. Начинай с прямого ответа на вопрос. Отвечай "
    "на ты, по-русски, в 1–3 предложениях без Markdown. Сохраняй язык ответа до конца. "
    "Давай одно точное объяснение простыми словами. Без вступления, аналогий и повторов. Пример приводи, только если его попросили. "
    "Ты ведёшь разговор. Действия Windows выполняет отдельный обработчик. "
    "Не выдумывай результаты действий, доступ к экрану или к файлам."
)
history: deque[dict[str, str]] = deque(maxlen=12)
enabled = False
MEMORY_MESSAGE = (
    "Память компьютера почти заполнена. Разговорная модель сейчас слишком тяжёлая. "
    "Освободи память или используй команды — браузер, музыка, окна и таймеры доступны без модели."
)


def memory_pressure() -> bool:
    """Не начинать тяжёлую генерацию при нехватке оперативной памяти."""
    memory = psutil.virtual_memory()
    return memory.percent >= 90 and memory.available < 1.5 * 2**30


_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


class LanguageDrift(ValueError):
    """Часть ответа сменила письменность посреди русскоязычного диалога."""


async def _request(messages: list[dict[str, str]]) -> str:
    """Дождаться ответа с ограничением времени и поддержкой остановки."""
    payload = {"model": MODEL, "messages": messages, "stream": True,
               "keep_alive": "30s",
               "options": {"num_predict": 140, "num_ctx": 2048, "temperature": 0.25}}

    user_text = messages[-1]["content"]
    russian = (bool(re.search("[а-яё]", user_text, re.I)) and not _CJK.search(user_text)
               and not re.search(r"китай|япон|корейск|иероглиф|перев[ео]д", user_text, re.I))

    async def stream(session: aiohttp.ClientSession) -> str:
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
                    if russian and _CJK.search(token):
                        prefix = "".join(parts) + _CJK.split(token, maxsplit=1)[0]
                        complete = re.match(r"(?s)^(.+[.!?])(?:\s|$)", prefix)
                        if complete:
                            return complete[1].strip()
                        raise LanguageDrift("Не завершена фраза на русском языке")
                    parts.append(token)
                    if len(parts) == 1:
                        speech.publish("state", "Пишу ответ…")
                    speech.publish("delta", token)
                if data.get("done"):
                    answer = "".join(parts).strip()
                    if answer:
                        return answer
                    break
            raise ValueError("Ответ модели не завершён")

    async def generate(session: aiohttp.ClientSession) -> str:
        try:
            return await stream(session)
        except LanguageDrift:
            speech.publish("end_draft", "")
            payload["messages"] = [*messages[:-1], {"role": "user", "content":
                user_text + "\nОтветь одним законченным предложением на русском языке."}]
            try:
                return await stream(session)
            except LanguageDrift:
                return "Не получилось сформулировать ответ по-русски. Перефразируй вопрос."

    timeout = aiohttp.ClientTimeout(total=35, connect=3)
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


def reply(text: str, *, context: tuple[str, str] = ("", "")) -> None:
    """Озвучить ответ и сохранить в памяти только завершённый обмен."""
    if memory_pressure():
        speech.speak(MEMORY_MESSAGE)
        return
    if len(text) > 2000:
        speech.speak("Раздели, пожалуйста, вопрос на несколько частей.")
        return
    user = {"role": "user", "content": text}
    try:
        messages = [{"role": "system", "content": SYSTEM}, *history]
        if context[1]:
            messages.append({"role": "user", "content": "Справка приложения о последнем действии: "
                             + json.dumps(context, ensure_ascii=False)})
        speech.publish("state", "Ожидаю локальную модель…")
        answer = asyncio.run(_request([*messages, user]))
    except (aiohttp.ClientError, OSError, asyncio.TimeoutError, ValueError,
            KeyError, TypeError, AttributeError):
        logging.getLogger(__name__).warning("Ошибка локальной Ollama", exc_info=True)
        if not speech.is_cancelled():
            speech.speak("Разговорная модель сейчас недоступна. Проверь Ollama "
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
