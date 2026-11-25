"""Настоящие UIA-кнопки Chromium на локальной подменённой странице, без аккаунта."""

import os
import json
from pathlib import Path
import subprocess
import sys
import time

import comtypes.client
from playwright.sync_api import sync_playwright
import psutil
import win32gui
import win32process

import yandex_media as media


def test_real_chromium_accessibility_player(tmp_path, monkeypatch):
    started = time.monotonic()
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(root / "tools/browser"))
    profile = tmp_path / "owned-fixture-profile"
    with sync_playwright() as runtime:
        context = runtime.chromium.launch_persistent_context(
            str(profile), headless=False, args=["--force-renderer-accessibility"]
        )
        try:
            # В сеть не уходит ни одного запроса и не используется личный профиль.
            context.route(
                "**/*",
                lambda route: route.fulfill(
                    content_type="text/html; charset=utf-8",
                    body="""<html lang="ru"><title>Яндекс Музыка — Varya test fixture</title><body><button aria-label="Воспроизведение" onclick="this.setAttribute('aria-label',this.getAttribute('aria-label')==='Пауза'?'Воспроизведение':'Пауза')">Play</button></body></html>""",
                ),
            )
            page = context.pages[0]
            page.goto("https://music.yandex.ru/")
            page.bring_to_front()
            ui = comtypes.client.GetModule("UIAutomationCore.dll")
            client = comtypes.client.CreateObject(
                ui.CUIAutomation, interface=ui.IUIAutomation
            )
            owners = set()
            for process in psutil.process_iter(["pid", "name", "cmdline"]):
                if process.info["name"] == "chrome.exe" and str(profile) in " ".join(
                    process.info["cmdline"] or []
                ):
                    owners.add(process.pid)
            assert owners
            documents = []
            handles = []

            def collect(hwnd, _):
                if win32process.GetWindowThreadProcessId(hwnd)[1] in owners:
                    owner = client.ElementFromHandle(hwnd)
                    found = owner.FindAll(
                        ui.TreeScope_Descendants,
                        client.CreatePropertyCondition(
                            ui.UIA_ControlTypePropertyId, ui.UIA_DocumentControlTypeId
                        ),
                    )
                    for i in range(found.Length):
                        item = found.GetElement(i)
                        if not item.CurrentIsOffscreen:
                            documents.append(item)
                            handles.append(hwnd)
                return True

            deadline = time.monotonic() + 5
            while not documents and time.monotonic() < deadline:
                win32gui.EnumWindows(collect, None)
                if not documents:
                    time.sleep(0.1)
            assert documents
            target = media.UIAPage(documents[0], client, ui)
            target._verify()
            assert media.operate(target, "resume")["ok"]
            assert page.get_by_role("button", name="Пауза", exact=True).count() == 1
            assert media.operate(target, "pause")["ok"]
            assert (
                page.get_by_role("button", name="Воспроизведение", exact=True).count()
                == 1
            )
            binary_root = os.environ.get("VARYA_TEST_FROZEN_ROOT")
            command = (
                [str(Path(binary_root) / "engine/Varya.Engine.exe")]
                if binary_root
                else [sys.executable, str(root / "engine_host.py")]
            )
            helper_started = time.monotonic()
            result = subprocess.run(
                command + ["--yandex-media", "resume", str(handles[0])],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=12,
                check=True,
            )
            response = json.loads(result.stdout)
            assert response["ok"], response
            assert page.get_by_role("button", name="Пауза", exact=True).count() == 1
            print(
                "UIA helper seconds:",
                round(time.monotonic() - helper_started, 2),
                flush=True,
            )
            page.goto("https://example.invalid/")
            try:
                target.invoke("Воспроизведение")
            except Exception:
                pass
            else:
                raise AssertionError("Старая цель приняла действие после смены сайта")
        finally:
            print(
                "UIA fixture work seconds:",
                round(time.monotonic() - started, 2),
                flush=True,
            )
            context.close()
        print(
            "UIA fixture total seconds:",
            round(time.monotonic() - started, 2),
            flush=True,
        )
