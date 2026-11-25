"""Управление только плеером Яндекс Музыки через доступные элементы Windows."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from urllib.parse import urlsplit

ACTIONS = {"resume", "wave", "pause", "next", "previous", "status"}
PLAY = {"воспроизведение", "воспроизвести", "продолжить воспроизведение", "play"}
PAUSE = {"пауза", "приостановить", "пауза моей волны", "pause"}
WAVE = {
    "воспроизведение моей волны",
    "воспроизвести мою волну",
    "включить мою волну",
    "моя волна",
}
BUTTONS = {
    "pause": PAUSE,
    "resume": PLAY,
    "wave": WAVE,
    "next": {"следующий трек", "следующая песня"},
    "previous": {"предыдущий трек", "предыдущая песня"},
}


def key(value: str) -> str:
    """Нормализовать доступную подпись, сохранив точное сравнение целого имени."""
    return " ".join(
        value.casefold().replace("ё", "е").replace("«", "").replace("»", "").split()
    ).strip(" .")


def music_url(value: str) -> bool:
    """Проверить настоящий адрес страницы, а не название вкладки."""
    value = value.strip()
    if "://" not in value:
        value = "https://" + value
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.hostname in {"music.yandex.ru", "music.yandex.com"}
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class Snapshot:
    """Наблюдаемые подписи плеера и текст его страницы."""

    names: tuple[str, ...]
    text: str = ""
    title: str = ""

    @property
    def playing(self) -> bool:
        return any(key(name) in PAUSE for name in self.names)


def operate(page, action: str) -> dict:
    """Нажать однозначную медиакнопку и проверить ответ интерфейса плеера."""
    if action not in ACTIONS:
        raise ValueError("Неизвестная команда Яндекс Музыки")
    before = page.snapshot()
    content = key(before.text)
    for fragment, message in (
        (
            "недоступна в вашем регионе",
            "Яндекс Музыка недоступна в регионе подключения.",
        ),
        ("вы не робот", "Яндекс просит пройти CAPTCHA в открытой вкладке."),
        (
            "войдите, чтобы слушать",
            "Войди в Яндекс Музыку в браузере и повтори просьбу.",
        ),
        ("оформите подписку", "Яндекс просит оформить подписку. Варя её не оформляет."),
    ):
        if fragment in content:
            return {"ok": False, "text": message}
    if action == "status":
        state = (
            "Воспроизведение включено"
            if before.playing
            else "Воспроизведение не подтверждено"
        )
        return {"ok": True, "text": state + " в Яндекс Музыке. " + before.title[:180]}
    if action == "resume" and before.playing:
        return {"ok": True, "text": "Яндекс Музыка уже воспроизводит музыку."}
    choices = [name for name in before.names if key(name) in BUTTONS[action]]
    if action == "resume" and not choices:
        choices = [name for name in before.names if key(name) in WAVE]
    if len(choices) != 1:
        return {
            "ok": False,
            "text": "Страница Яндекс Музыки открыта, но не вижу однозначной кнопки плеера. Проверь вход и доступ к музыке в этой вкладке. Можно один раз включить трек на сайте, затем попросить Варю продолжить или поставить на паузу.",
        }
    page.invoke(choices[0])
    if action in {"next", "previous"}:
        return {"ok": True, "text": "Нажала «" + choices[0] + "» в Яндекс Музыке."}
    expected = action != "pause"
    deadline = monotonic() + 3
    while monotonic() < deadline:
        after = page.snapshot()
        if after.playing == expected and (
            expected or any(key(name) in PLAY | WAVE for name in after.names)
        ):
            return {
                "ok": True,
                "text": "Яндекс Музыка на паузе."
                if not expected
                else "Воспроизведение в Яндекс Музыке включено.",
            }
        sleep(0.15)
    return {
        "ok": False,
        "text": "Нажала кнопку Яндекс Музыки, но плеер не подтвердил изменение состояния. Проверь вход, подписку и звук на странице.",
    }


class UIAPage:
    """Доступ только к документу с проверенным адресом music.yandex.ru."""

    def __init__(self, root, client, ui):
        self.root, self.client, self.ui = root, client, ui

    def _verify(self):
        for prop in (
            self.ui.UIA_ValueValuePropertyId,
            self.ui.UIA_LegacyIAccessibleValuePropertyId,
        ):
            try:
                if music_url(str(self.root.GetCurrentPropertyValue(prop) or "")):
                    return
            except Exception:
                continue
        raise ValueError(
            "Адрес вкладки изменился. Управление Яндекс Музыкой остановлено."
        )

    def _buttons(self):
        self._verify()
        ui = self.ui
        values = self.root.FindAll(
            ui.TreeScope_Descendants,
            self.client.CreatePropertyCondition(
                ui.UIA_ControlTypePropertyId, ui.UIA_ButtonControlTypeId
            ),
        )
        if values.Length > 500:
            raise ValueError(
                "На странице слишком много кнопок для однозначного управления."
            )
        result = []
        for i in range(values.Length):
            item = values.GetElement(i)
            if (
                item.CurrentIsEnabled
                and not item.CurrentIsOffscreen
                and not item.CurrentIsPassword
            ):
                result.append(item)
        return result

    def snapshot(self) -> Snapshot:
        buttons = self._buttons()
        text = ""
        try:
            if self.root.GetCurrentPropertyValue(
                self.ui.UIA_IsTextPatternAvailablePropertyId
            ):
                pattern = self.root.GetCurrentPattern(
                    self.ui.UIA_TextPatternId
                ).QueryInterface(self.ui.IUIAutomationTextPattern)
                text = pattern.DocumentRange.GetText(4000)
        except Exception:
            pass
        return Snapshot(
            tuple(item.CurrentName or "" for item in buttons),
            text,
            self.root.CurrentName or "",
        )

    def invoke(self, name: str) -> None:
        matches = [item for item in self._buttons() if item.CurrentName == name]
        if len(matches) != 1:
            raise ValueError("Кнопка плеера изменилась. Повтори просьбу.")
        self._verify()
        matches[0].GetCurrentPattern(self.ui.UIA_InvokePatternId).QueryInterface(
            self.ui.IUIAutomationInvokePattern
        ).Invoke()


def find_page(target_handle: int = 0):
    """Найти единственную видимую вкладку музыки в браузере по её реальному URL."""
    import comtypes.client
    import psutil
    import win32gui
    import win32process

    ui = comtypes.client.GetModule("UIAutomationCore.dll")
    client = comtypes.client.CreateObject(ui.CUIAutomation, interface=ui.IUIAutomation)
    handles = []

    def collect(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            handles.append(hwnd)
        return True

    win32gui.EnumWindows(collect, None)
    pages = []
    for hwnd in handles:
        if target_handle and hwnd != target_handle:
            continue
        # Не трогаем соседние вкладки, фоновые окна и посторонние программы.
        title = key(win32gui.GetWindowText(hwnd))
        if not any(
            label in title for label in ("яндекс музыка", "yandex music", "вы не робот")
        ):
            continue
        try:
            pid = win32process.GetWindowThreadProcessId(hwnd)[1]
            if psutil.Process(pid).name().casefold() not in {
                "browser.exe",
                "chrome.exe",
                "msedge.exe",
            }:
                continue
            owner = client.ElementFromHandle(hwnd)
            documents = owner.FindAll(
                ui.TreeScope_Descendants,
                client.CreatePropertyCondition(
                    ui.UIA_ControlTypePropertyId, ui.UIA_DocumentControlTypeId
                ),
            )
            for i in range(min(documents.Length, 20)):
                document = documents.GetElement(i)
                if document.CurrentIsOffscreen:
                    continue
                page = UIAPage(document, client, ui)
                try:
                    page._verify()
                except ValueError:
                    continue
                pages.append(page)
        except (psutil.Error, OSError):
            continue
    if len(pages) > 1:
        raise ValueError(
            "Открыто несколько окон Яндекс Музыки. Оставь нужную вкладку видимой в одном окне и повтори просьбу."
        )
    return pages[0] if pages else None


def run_helper(action: str, target_handle: int = 0) -> None:
    """Изолировать обращения UI Automation, которые могут зависеть от браузера."""
    try:
        if action not in ACTIONS:
            raise ValueError("Неизвестная медиакоманда")
        deadline = monotonic() + 6
        page = None
        while monotonic() < deadline:
            page = find_page(target_handle)
            if page is not None:
                break
            sleep(0.3)
        answer = (
            operate(page, action)
            if page
            else {
                "ok": False,
                "text": "Не вижу доступной вкладки Яндекс Музыки. Сделай её видимой в Яндекс Браузере, Chrome или Edge, дождись загрузки и повтори просьбу. Локальные файлы вместо неё не запускаю.",
            }
        )
    except Exception as error:
        answer = {
            "ok": False,
            "text": "Не удалось управлять плеером Яндекс Музыки. " + str(error)[:240],
        }
    print(json.dumps(answer, ensure_ascii=True), flush=True)


def control(action: str):
    """Ограничить время и отмену UIA-помощника, не оставляя его в памяти."""
    from command_results import Result
    import speech

    if action not in ACTIONS:
        raise ValueError("Неизвестная медиакоманда")
    if speech.is_cancelled():
        raise InterruptedError
    root = Path(os.environ.get("VARYA_HOME", Path(__file__).parent))
    arguments = [sys.executable]
    if not getattr(sys, "frozen", False):
        arguments.append(str(root / "engine_host.py"))
    arguments += ["--yandex-media", action]
    process = subprocess.Popen(
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = monotonic() + 12
        while process.poll() is None and monotonic() < deadline:
            if speech.is_cancelled():
                raise InterruptedError
            sleep(0.1)
        if process.poll() is None:
            return Result(
                "Браузер не ответил вовремя. Сделай вкладку Яндекс Музыки видимой и повтори просьбу.",
                ok=False,
            )
        output, _ = process.communicate(timeout=1)
        if process.returncode:
            raise OSError("Не удалось запустить помощник Яндекс Музыки")
        data = json.loads(output)
        return Result(str(data["text"]), ok=data["ok"] is True)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
