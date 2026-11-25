"""Сквозная проверка управляемого браузера на собственном локальном сайте."""

import http.server
from pathlib import Path
import threading
import time

import pytest

import browser_tools
import capabilities
import speech
from dialogue import Dialogue


def test_browser_fields_confirmation_and_navigation(monkeypatch):
    browsers = Path(__file__).resolve().parents[1] / "tools" / "browser"
    if not list(browsers.glob("chromium-*")):
        pytest.skip("Для сквозной проверки нужен подготовленный Chromium")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/slow":
                threading.Event().wait(8)
            data = b'<html><head><title>Varya test</title></head><body><h1>Fixture</h1><label for="name">Name</label><input id="name"><button onclick="document.querySelector(\'h1\').textContent=\'Clicked\'">Apply</button></body></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            try:
                self.wfile.write(data)
            except ConnectionAbortedError:
                pass
            except BrokenPipeError:
                pass

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = browser_tools.open_url(f"http://127.0.0.1:{server.server_port}/")
        assert "Varya test" in result
        assert "Fixture" in browser_tools.read_page()
        assert "Apply" in browser_tools.elements()
        session = Dialogue()
        import json

        capabilities.request(
            session, "browser_fill", json.dumps({"name": "Name", "text": "Varya"})
        )
        assert browser_tools.field_value("Name") == ""
        capabilities.confirmation(session, "да")
        assert browser_tools.field_value("Name") == "Varya"
        capabilities.request(session, "browser_click", "Apply")
        capabilities.confirmation(session, "нет")
        assert browser_tools.text("h1") == "Fixture"
        capabilities.request(session, "browser_click", "Apply")
        capabilities.confirmation(session, "да")
        assert browser_tools.text("h1") == "Clicked"
        assert "Varya test" in browser_tools.tabs()
        cancel = threading.Event()
        monkeypatch.setattr(speech, "cancel_event", cancel)

        def stop():
            cancel.set()
            browser_tools.abort()

        timer = threading.Timer(0.3, stop)
        timer.start()
        start = time.monotonic()
        with pytest.raises(InterruptedError):
            browser_tools.open_url(f"http://127.0.0.1:{server.server_port}/slow")
        timer.join()
        assert time.monotonic() - start < 4
        cancel.clear()
        assert "Varya test" in browser_tools.open_url(
            f"http://127.0.0.1:{server.server_port}/"
        )
        browser_tools._run(lambda: browser_tools._context.close())
        assert "Varya test" in browser_tools.open_url(
            f"http://127.0.0.1:{server.server_port}/"
        )
    finally:
        browser_tools.close()
        server.shutdown()
        server.server_close()
