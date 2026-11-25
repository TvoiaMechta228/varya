"""Управляемый браузер в отдельном асинхронном потоке с отменой запросов."""

import asyncio
from concurrent.futures import CancelledError, Future, TimeoutError as FutureTimeout
import hashlib
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Callable, Coroutine
from urllib.parse import urlsplit

import settings
import speech

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_serial: asyncio.Lock | None = None
_guard = threading.Lock()
_pending: Future | None = None
_runtime = None
_context = None
_page = None
OPERATION_TIMEOUT = 30.0


def page_problem(url: str, title: str, body: str) -> str | None:
    """Распознать ограничения сайта и предложить понятный следующий шаг."""
    host = (urlsplit(url).hostname or "").lower()
    content = (title + "\n" + body).casefold()
    if host in {"ya.ru", "yandex.ru"} or host.endswith((".ya.ru", ".yandex.ru")):
        if (
            "/showcaptcha" in url
            or "подтвердите, что запросы" in content
            or "вы не робот" in content
        ):
            return "Яндекс просит подтвердить, что ты не робот. Пройди проверку в открытой вкладке. Обычный поиск можно открыть командой «найди в Яндексе …» в твоём Яндекс Браузере."
        if (
            "недоступна в вашем регионе" in content
            or "недоступен в вашем регионе" in content
        ):
            return "Яндекс сообщает, что сервис недоступен в текущем регионе подключения. Варя не может изменить ограничение сайта. Можно воспроизвести локальную музыку."
    return None


def valid_url(value: str) -> str:
    """Разрешить веб-адреса без встроенного пароля и управляющих символов."""
    value = value.strip()
    if not value or any(ord(char) < 32 for char in value):
        raise ValueError("Укажи адрес сайта")
    if re.match(r"^[a-z][a-z0-9+.-]*:", value, re.I) and not value.startswith(
        ("http://", "https://")
    ):
        if not re.match(r"^[\w.-]+:\d+(?:/|$)", value):
            raise ValueError("Доступны только веб-адреса http и https")
    if "://" not in value:
        value = "https://" + value
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise ValueError("Доступны адреса http и https без пароля в ссылке")
    return value


def _start() -> asyncio.AbstractEventLoop:
    global _thread, _loop, _serial
    with _guard:
        if _loop is None:
            ready = threading.Event()

            def worker() -> None:
                global _loop, _serial
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                _loop, _serial = loop, asyncio.Lock()
                ready.set()
                loop.run_forever()
                remaining = asyncio.all_tasks(loop)
                for task in remaining:
                    task.cancel()
                if remaining:
                    loop.run_until_complete(
                        asyncio.gather(*remaining, return_exceptions=True)
                    )
                loop.close()

            _thread = threading.Thread(target=worker, name="VaryaBrowser", daemon=True)
            _thread.start()
            if not ready.wait(5):
                raise OSError("Не удалось подготовить поток браузера")
        return _loop


async def _execute(operation: Callable[[], Coroutine], force: bool) -> Any:
    global _page
    async with _serial:
        try:
            if not force and speech.is_cancelled():
                raise InterruptedError
            return (
                await operation()
                if force
                else await asyncio.wait_for(operation(), OPERATION_TIMEOUT)
            )
        except TimeoutError as error:
            if _page is not None:
                try:
                    await asyncio.wait_for(_page.close(), timeout=2)
                except Exception:
                    pass
                _page = None
            raise OSError(
                "Сайт не ответил вовремя. Повтори просьбу или открой Яндекс в обычном браузере."
            ) from error
        except asyncio.CancelledError:
            # Закрытие активной вкладки останавливает навигацию в самом Chromium.
            # Остальные вкладки и профиль сохраняются.
            if _page is not None:
                try:
                    await asyncio.wait_for(_page.close(), timeout=2)
                except Exception:
                    pass
                _page = None
            raise


def _run(operation: Callable[[], Coroutine], *, force: bool = False) -> Any:
    global _pending
    loop = _start()
    future = asyncio.run_coroutine_threadsafe(_execute(operation, force), loop)
    with _guard:
        _pending = future
    try:
        while True:
            if not force and speech.is_cancelled():
                future.cancel()
                raise InterruptedError("Действие браузера остановлено")
            try:
                return future.result(timeout=0.1)
            except FutureTimeout:
                if future.done():
                    return future.result()
                continue
            except CancelledError:
                raise InterruptedError("Действие браузера остановлено") from None
    except Exception as error:
        from playwright.async_api import Error

        if isinstance(error, Error):
            message = str(error).split("Call log:", 1)[0][:350]
            raise OSError("Браузер не завершил действие. " + message) from error
        raise
    finally:
        with _guard:
            if _pending is future:
                _pending = None


def abort() -> None:
    """Из любого потока отменить текущую браузерную операцию без завершения браузера."""
    with _guard:
        future = _pending
    if future is not None:
        future.cancel()


async def _owner():
    global _runtime, _context
    if _context is None:
        from playwright.async_api import async_playwright

        root = Path(os.environ.get("VARYA_HOME", Path(__file__).parent))
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(root / "tools/browser"))
        if _runtime is None:
            _runtime = await async_playwright().start()
        try:
            _context = await _runtime.chromium.launch_persistent_context(
                str(settings.DATA_DIR / "browser-profile"),
                headless=False,
                locale="ru-RU",
                no_viewport=True,
                chromium_sandbox=True,
                args=["--start-maximized"],
                timeout=20000,
                ignore_default_args=[
                    "--password-store=basic",
                    "--use-mock-keychain",
                    "--unsafely-disable-devtools-self-xss-warnings",
                ],
            )
            _context.set_default_timeout(5000)
            owner = _context

            def closed(*_) -> None:
                global _context, _page
                if _context is owner:
                    _context = _page = None

            owner.on("close", closed)
        except BaseException:
            await _runtime.stop()
            _runtime = None
            raise
    return _context


async def _current():
    global _page
    if _context is None:
        raise ValueError("Сначала открой сайт командой «открой сайт …»")
    if _page is None or _page.is_closed():
        _page = _context.pages[-1] if _context.pages else await _context.new_page()
    return _page


async def _target(kind: str, name: str):
    current = await _current()
    if kind == "field":
        found = current.get_by_label(name, exact=True).or_(
            current.get_by_placeholder(name, exact=True)
        )
    else:
        found = current.get_by_role("button", name=name, exact=True).or_(
            current.get_by_role("link", name=name, exact=True)
        )
    matches = [
        item
        for item in await found.all()
        if await item.is_visible() and await item.is_enabled()
    ]
    if len(matches) != 1:
        raise ValueError(f"Нашла элементов «{name}» — {len(matches)}. Уточни подпись.")
    return matches[0]


def target(kind: str, name: str) -> None:
    """Проверить однозначность элемента перед подтверждением."""

    async def operation():
        await _target(kind, name)

    _run(operation)


def open_url(value: str, *, reuse: bool = True) -> str:
    """Открыть новую вкладку и проверить фактический адрес и HTTP-ответ."""
    url = valid_url(value)

    async def operation():
        global _page
        owner = await _owner()
        wanted = urlsplit(url)
        # Повторные запросы поиска/сервиса используют одну вкладку того же сайта.
        is_search = (
            wanted.hostname in {"ya.ru", "yandex.ru"}
            and wanted.path.rstrip("/") == "/search"
        )
        existing = (
            next(
                (
                    p
                    for p in owner.pages
                    if p.url == url
                    or (
                        is_search
                        and urlsplit(p.url).netloc == wanted.netloc
                        and urlsplit(p.url).path.rstrip("/") == "/search"
                    )
                ),
                None,
            )
            if reuse
            else None
        )
        _page = (
            existing
            or next((p for p in owner.pages if p.url == "about:blank"), None)
            or await owner.new_page()
        )
        if existing and existing.url == url:
            await _page.bring_to_front()
        else:
            response = await _page.goto(url, wait_until="commit", timeout=15000)
            if response is not None and response.status >= 400:
                raise OSError(f"Сайт вернул HTTP {response.status}. Адрес {_page.url}")
            try:
                await _page.wait_for_load_state("domcontentloaded", timeout=1500)
            except asyncio.TimeoutError:
                pass
            except Exception as error:
                from playwright.async_api import TimeoutError as BrowserTimeout

                if not isinstance(error, BrowserTimeout):
                    raise
        await _page.bring_to_front()
        title = await _page.title()
        try:
            body = await _page.locator("body").inner_text(timeout=1500)
        except Exception as error:
            from playwright.async_api import TimeoutError as BrowserTimeout

            if not isinstance(error, BrowserTimeout):
                raise
            body = ""
        problem = page_problem(_page.url, title, body[:4000])
        if problem:
            return problem
        return f"Открыта страница «{title or wanted.hostname}». Адрес {_page.url}"

    return _run(operation)


def current_url() -> str:
    """Вернуть адрес выбранной вкладки."""

    async def operation():
        return (await _current()).url

    return _run(operation)


def tabs() -> str:
    """Перечислить живые вкладки своего браузера."""

    async def operation():
        if _context is None:
            return "Управляемый браузер ещё не открыт. Скажи «открой сайт …»."
        return "\n".join(
            [
                f"{i}. {await page.title()} — {page.url}"
                for i, page in enumerate(_context.pages, 1)
            ]
        )

    return _run(operation)


def select_tab(index: int) -> str:
    """Выбрать вкладку по номеру последнего списка."""

    async def operation():
        global _page
        if _context is None or not 1 <= index <= len(_context.pages):
            raise ValueError("Укажи номер из списка вкладок браузера")
        _page = _context.pages[index - 1]
        await _page.bring_to_front()
        return "Выбрана вкладка «" + await _page.title() + "»."

    return _run(operation)


def text(selector: str = "body") -> str:
    """Прочитать ограниченный видимый текст DOM для инструмента чтения."""

    async def operation():
        current = await _current()
        return (await current.locator(selector).inner_text(timeout=5000))[:16000]

    return _run(operation)


def links(selector: str) -> list[str]:
    """Получить адреса ссылок по фиксированному селектору инструмента поиска."""

    async def operation():
        current = await _current()
        return [
            await item.get_attribute("href") or ""
            for item in (await current.locator(selector).all())[:20]
        ]

    return _run(operation)


def field_value(name: str) -> str:
    """Прочитать значение однозначно указанного поля для проверки результата."""

    async def operation():
        return await (await _target("field", name)).input_value()

    return _run(operation)


def read_page() -> str:
    """Прочитать страницу вместе с адресом источника."""
    return current_url() + "\n\n" + text()


def elements() -> str:
    """Перечислить подписи доступных видимых кнопок, ссылок и полей."""

    async def operation():
        current = await _current()
        rows = []
        for role in ("button", "link", "textbox", "combobox"):
            for item in (await current.get_by_role(role).all())[:60]:
                if await item.is_visible():
                    name = await item.evaluate(
                        "el => el.getAttribute('aria-label') || el.getAttribute('placeholder') || (el.labels ? [...el.labels].map(x => x.innerText).join(' ') : '') || el.innerText || ''"
                    )
                    if name.strip():
                        rows.append(f"{role} — {name.strip()[:100]}")
        return "\n".join(rows[:80]) or "Подписанных элементов не найдено."

    return _run(operation)


async def _fingerprint() -> str:
    current = await _current()
    fields = await current.locator("input,textarea,select").evaluate_all(
        "items => items.map(el => [el.name, el.type, el.value])"
    )
    body = (await current.locator("body").inner_text())[:16000]
    return hashlib.sha256(
        json.dumps([current.url, body, fields], ensure_ascii=False).encode()
    ).hexdigest()


def fingerprint() -> str:
    """Зафиксировать адрес, текст и форму без сохранения открытых паролей."""
    return _run(_fingerprint)


def interact(action: str, data: dict) -> str:
    """Выполнить подтверждённое действие после повторной проверки страницы."""

    async def operation():
        current = await _current()
        if (
            data.get("url") != current.url
            or data.get("_fingerprint") != await _fingerprint()
        ):
            raise ValueError(
                "Страница изменилась после предложения. Запроси действие ещё раз."
            )
        item = await _target(
            "field" if action == "browser_fill" else "button", data["name"]
        )
        if action == "browser_fill":
            await item.fill(data["text"])
            if await item.input_value() != data["text"]:
                raise OSError("Поле не сохранило введённое значение")
            return f"Поле «{data['name']}» заполнено."
        await item.click()
        return f"Нажата кнопка «{data['name']}». Текущая страница — {current.url}."

    return _run(operation)


def close() -> None:
    """Закрыть профиль и остановить служебный поток браузера."""
    global _context, _runtime, _page, _loop, _thread, _serial
    if _loop is None:
        return
    abort()

    async def cleanup():
        global _context, _runtime, _page
        try:
            if _context is not None:
                await asyncio.wait_for(_context.close(), 5)
        finally:
            if _runtime is not None:
                await _runtime.stop()
            _context = _runtime = _page = None

    future = asyncio.run_coroutine_threadsafe(_execute(cleanup, True), _loop)
    try:
        future.result(timeout=8)
    except Exception:
        future.cancel()
    finally:
        _loop.call_soon_threadsafe(_loop.stop)
        if _thread is not None:
            _thread.join(timeout=3)
        _context = _runtime = _page = _loop = _thread = _serial = None
