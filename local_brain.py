"""Изолированная локальная модель llama.cpp с ограничением времени и памяти простоя."""

import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
from urllib.request import Request, urlopen

import aiohttp

import speech

_process = None
_port = 0
_key = secrets.token_urlsafe(32)
_lock = threading.RLock()
_active = 0
_last_used = 0.0


def close() -> None:
    """Завершить только запущенный этим процессом сервер модели."""
    global _process
    with _lock:
        if _process is not None:
            _process.terminate()
            try:
                _process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _process.kill()
                _process.wait(timeout=3)
            _process = None


def release_idle() -> None:
    """Освободить модель через полминуты после завершённого обращения."""
    with _lock:
        if _active == 0 and time.monotonic() - _last_used > 30:
            close()


def ensure_server() -> str:
    """Поднять сервер на свободном loopback-порту с одноразовым API-ключом."""
    global _process, _port
    with _lock:
        if _process is None or _process.poll() is not None:
            root = Path(os.environ.get("VARYA_HOME", Path(__file__).parent))
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                _port = listener.getsockname()[1]
            command = [
                str(root / "tools" / "llama" / "llama-server.exe"),
                "-m",
                str(root / "models" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"),
                "--host",
                "127.0.0.1",
                "--port",
                str(_port),
                "--api-key",
                _key,
                "-c",
                "8192",
                "-t",
                "4",
                "-np",
                "1",
                "--no-webui",
            ]
            _process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
    url = f"http://127.0.0.1:{_port}"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if speech.is_cancelled():
            raise InterruptedError
        if _process is None or _process.poll() is not None:
            raise OSError("Не удалось запустить локальную модель")
        try:
            request = Request(
                url + "/health", headers={"Authorization": "Bearer " + _key}
            )
            with urlopen(request, timeout=0.5) as response:
                if response.status == 200:
                    return url
        except OSError:
            time.sleep(0.1)
    raise TimeoutError("Локальная модель не загрузилась за 30 секунд")


async def _complete(
    messages: list[dict], schema: dict | None = None, max_tokens: int = 512
) -> str:
    """Получить проверяемое намерение или поток русского ответа локальной модели."""
    global _active, _last_used
    with _lock:
        _active += 1
    try:
        speech.publish("state", "Обдумываю просьбу локально…")
        url = await asyncio.to_thread(ensure_server)
        if len(messages) > 2:
            budget = 6500 - len(messages[0]["content"]) - len(messages[-1]["content"])
            middle = []
            for item in reversed(messages[1:-1]):
                if budget <= 0:
                    break
                text = item["content"][: min(budget, 1500)]
                middle.append({**item, "content": text})
                budget -= len(text)
            messages = [messages[0], *reversed(middle), messages[-1]]
        payload = {
            "messages": messages,
            "stream": schema is None,
            "max_tokens": max_tokens,
            "temperature": 0 if schema else 0.25,
        }
        if schema:
            payload["response_format"] = {"type": "json_object", "schema": schema}
        timeout = aiohttp.ClientTimeout(total=90, connect=2)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as client:
            async with client.post(
                url + "/v1/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer " + _key},
            ) as response:
                response.raise_for_status()
                if schema:
                    body = await response.json()
                    return body["choices"][0]["message"]["content"]
                parts = []
                async for line in response.content:
                    if speech.is_cancelled():
                        raise InterruptedError
                    if not line.startswith(b"data: "):
                        continue
                    data = line[6:].strip()
                    if data == b"[DONE]":
                        break
                    token = (
                        json.loads(data)["choices"][0]
                        .get("delta", {})
                        .get("content", "")
                    )
                    if token:
                        parts.append(token)
                        speech.publish("delta", token)
                answer = "".join(parts).strip()
                if not answer:
                    raise ValueError("Модель вернула пустой ответ")
                return answer
    finally:
        with _lock:
            _active -= 1
            _last_used = time.monotonic()


async def complete(
    messages: list[dict], schema: dict | None = None, max_tokens: int = 512
) -> str:
    """Прерывать ожидание загрузки, prefill и ответа, включая паузу до первого токена."""
    task = asyncio.create_task(_complete(messages, schema, max_tokens))
    try:
        while not task.done():
            if speech.is_cancelled():
                raise InterruptedError("Ответ модели остановлен")
            await asyncio.wait({task}, timeout=0.05)
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
