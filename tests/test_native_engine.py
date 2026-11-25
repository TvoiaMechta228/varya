"""Проверка реального канала WinUI–движок без микрофона и сетевых действий."""

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def test_engine_protocol_preserves_tasks_and_stop(tmp_path):
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen(
        [sys.executable, "-u", "engine_host.py"], cwd=root,
        env={**os.environ, "PYTHONUTF8": "1", "LOCALAPPDATA": str(tmp_path),
             "VARYA_HOME": str(root)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        encoding="utf-8",
    )
    events = queue.Queue()

    def reader():
        for line in process.stdout:
            events.put(json.loads(line))

    threading.Thread(target=reader, daemon=True).start()

    def receive(kind):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            event = events.get(timeout=max(.1, deadline - time.monotonic()))
            assert event["kind"] != "fatal", event
            if event["kind"] == kind:
                return event["value"]
        raise AssertionError(kind)

    def send(command):
        process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
        process.stdin.flush()

    try:
        assert receive("ready")["offline"]
        send({"op": "typed", "text": "таймер работа на минуту"})
        assert "запущен" in receive("assistant").lower()
        receive("idle")
        send({"op": "typed", "text": "запомни Новая Варя"})
        assert receive("assistant") == "Записала."
        receive("idle")
        send({"op": "stop"})
        assert receive("idle")["listening"] is False
        timers = json.loads((tmp_path / "Varya" / "timers.json").read_text(encoding="utf-8"))
        assert timers[0]["name"] == "работа"
        send({"op": "close"})
        assert process.wait(timeout=6) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
