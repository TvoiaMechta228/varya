"""Проверка готового движка через настоящий протокол, документы и локальный сайт."""

import argparse
import http.server
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import wave


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--voice", action="store_true")
    parser.add_argument("--model", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    results = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/slow":
                threading.Event().wait(8)
            body = b'<html><title>Varya smoke</title><body><h1>Ready</h1><label for="name">Name</label><input id="name"><button onclick="document.querySelector(\'h1\').textContent=\'Confirmed\'">Apply</button></body></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError):
                pass

        def log_message(self, *_):
            pass

    with tempfile.TemporaryDirectory(
        prefix="varya-frozen-qa-", ignore_cleanup_errors=True
    ) as temporary:
        data = Path(temporary)
        events = queue.Queue()
        with (data / "engine.log").open("w", encoding="utf-8") as errors:
            process = subprocess.Popen(
                [str(root / "engine/Varya.Engine.exe")],
                cwd=root,
                env={
                    **os.environ,
                    "VARYA_HOME": str(root),
                    "LOCALAPPDATA": str(data),
                    "PYTHONUTF8": "1",
                },
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
                encoding="utf-8",
            )

            def reader():
                for line in process.stdout:
                    events.put(json.loads(line))

            threading.Thread(target=reader, daemon=True).start()

            def wait(kind: str, timeout: float = 45) -> list[dict]:
                received = []
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        item = events.get(timeout=0.2)
                    except queue.Empty:
                        if process.poll() is not None:
                            raise AssertionError(
                                "Движок завершился. "
                                + (data / "engine.log").read_text(encoding="utf-8")[
                                    -3000:
                                ]
                            )
                        continue
                    received.append(item)
                    if item["kind"] == "fatal":
                        raise AssertionError(item)
                    if item["kind"] == kind:
                        return received
                raise AssertionError(
                    "Не получено событие " + kind + " " + str(received)[-1000:]
                )

            def send(value: dict) -> None:
                process.stdin.write(json.dumps(value, ensure_ascii=True) + "\n")
                process.stdin.flush()

            def command(text: str, timeout: float = 45) -> list[dict]:
                started = time.monotonic()
                send({"op": "typed", "text": text})
                answer = wait("idle", timeout)
                results.append(
                    {
                        "command": text,
                        "seconds": round(time.monotonic() - started, 2),
                        "answers": [
                            item["value"]
                            for item in answer
                            if item["kind"] in {"assistant", "private_text", "notice"}
                        ],
                    }
                )
                print(json.dumps(results[-1], ensure_ascii=True), flush=True)
                return answer

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                assert wait("ready", 30)[-1]["value"]["version"] == "2.2.0"
                doc = data / "отчёт.docx"
                answer = command(f"Запиши в документ {doc} что встреча завтра")
                assert not doc.exists() and any(
                    "Подтверждаешь" in str(item["value"]) for item in answer
                )
                command("да")
                from docx import Document

                assert "встреча завтра" in "\n".join(
                    p.text for p in Document(doc).paragraphs
                )
                sheet = data / "бюджет.xlsx"
                command(f"в таблице «{sheet}» запиши в B2 1500")
                assert not sheet.exists()
                command("да")
                from openpyxl import load_workbook

                book = load_workbook(sheet, read_only=True)
                assert book.active["B2"].value == "1500"
                book.close()
                command("запомни обо мне мой любимый цвет зелёный")
                assert any(
                    "зелёный" in str(item["value"])
                    for item in command("что ты помнишь обо мне")
                )
                command("таймер работа на минуту")
                assert (
                    json.loads(
                        (data / "Varya/timers.json").read_text(encoding="utf-8")
                    )[0]["name"]
                    == "работа"
                )
                url = f"http://127.0.0.1:{server.server_port}/"
                assert any(
                    "Varya smoke" in str(item["value"])
                    for item in command("открой сайт " + url)
                )
                assert any(
                    "Name" in str(item["value"])
                    for item in command("покажи поля сайта")
                )
                command("в браузере заполни поле «Name» Варя")
                command("да")
                command("на сайте нажми Apply")
                command("нет")
                assert any(
                    "Ready" in str(item["value"])
                    for item in command("прочитай страницу")
                )
                command("на сайте нажми Apply")
                command("да")
                assert any(
                    "Confirmed" in str(item["value"])
                    for item in command("прочитай страницу")
                )
                send({"op": "typed", "text": "открой сайт " + url + "slow"})
                threading.Event().wait(0.5)
                started = time.monotonic()
                send({"op": "stop"})
                wait("idle", 6)
                assert time.monotonic() - started < 4
                assert any(
                    "Varya smoke" in str(item["value"])
                    for item in command("открой сайт " + url)
                )
                music = data / "music"
                music.mkdir()
                with wave.open(str(music / "Varya QA.wav"), "wb") as audio:
                    audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                    audio.writeframes(bytes(16000 * 2 * 5))
                send({"op": "settings", "values": {"music_root": str(music)}})
                wait("idle")
                assert any(
                    "Играет" in str(item["value"]) for item in command("включи музыку")
                )
                assert any(
                    "на паузе" in str(item["value"])
                    for item in command("поставь музыку на паузу")
                )
                if args.model:
                    answer = command("привет, как тебя зовут", 120)
                    assert any(
                        "варя" in str(item["value"]).casefold()
                        for item in answer
                        if item["kind"] == "assistant"
                    )
                if args.voice:
                    send({"op": "preview"})
                    answer = wait("idle", 45)
                    assert not any(item["kind"] == "notice" for item in answer)
                send({"op": "close"})
                assert process.wait(timeout=12) == 0
            finally:
                if process.poll() is None:
                    try:
                        send({"op": "close"})
                        process.wait(timeout=12)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                if process.poll() is None:
                    import psutil

                    for child in psutil.Process(process.pid).children(recursive=True):
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass
                    process.kill()
                    process.wait(timeout=5)
                server.shutdown()
                server.server_close()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(
                    {"passed": True, "results": results}, ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
        print("FROZEN SMOKE PASSED", flush=True)


if __name__ == "__main__":
    main()
