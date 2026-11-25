"""Загрузить закреплённые локальные модели и проверить контрольные суммы."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile

root = Path(__file__).resolve().parent
for item in json.loads((root / "model-manifest.json").read_text(encoding="utf-8")):
    path = root / "models" / item["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size == item["size"]:
        if hashlib.file_digest(path.open("rb"), "sha256").hexdigest() == item["sha256"]:
            continue
    temporary = path.with_suffix(path.suffix + ".partial")
    print("Загружаю", item["path"], flush=True)
    with (
        urllib.request.urlopen(item["url"] + "?download=true", timeout=60) as response,
        temporary.open("wb") as output,
    ):
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
    if (
        hashlib.file_digest(temporary.open("rb"), "sha256").hexdigest()
        != item["sha256"]
    ):
        raise ValueError("Не совпала контрольная сумма " + item["path"])
    temporary.replace(path)
archive = root / "build" / "llama-b12651.zip"
if not (root / "tools/llama/llama-server.exe").exists():
    archive.parent.mkdir(exist_ok=True)
    urllib.request.urlretrieve(
        "https://github.com/ggml-org/llama.cpp/releases/download/b12651/llama-b12651-bin-win-cpu-x64.zip",
        archive,
    )
    if (
        hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
        != "b2d794fa7587aba29f654d717ff9b73e31caeabf50a7766692903008bbe0855d"
    ):
        raise ValueError("Не совпала контрольная сумма llama.cpp")
    target = (root / "tools/llama").resolve()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        if any(
            not (target / name).resolve().is_relative_to(target)
            for name in bundle.namelist()
        ):
            raise ValueError("Недопустимый путь архива")
        bundle.extractall(target)
with zipfile.ZipFile(root / "models/vosk-model-small-ru-0.22.zip") as bundle:
    target = (root / "models").resolve()
    if any(
        not (target / name).resolve().is_relative_to(target)
        for name in bundle.namelist()
    ):
        raise ValueError("Недопустимый путь модели остановки речи")
    bundle.extractall(target)
subprocess.run(
    [sys.executable, "-m", "playwright", "install", "--no-shell", "chromium"],
    env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(root / "tools/browser")},
    check=True,
)
print("Локальные модели и браузер готовы.")
