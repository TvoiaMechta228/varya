"""Создание проекта по описанию с проверкой путей и синтаксиса, без автоматического запуска кода."""

import ast
import asyncio
import json
from pathlib import Path
import shutil
import uuid

import local_brain
import settings
import speech

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "files": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "required": ["name", "files"],
}


def validate(value: dict) -> list[dict]:
    """Отклонить выход за каталог, дубли, чрезмерные размеры и ошибочный Python/JSON."""
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("files"), list)
        or not 1 <= len(value["files"]) <= 12
    ):
        raise ValueError("Модель не подготовила корректный проект")
    names = set()
    total = 0
    files = []
    for item in value["files"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("content"), str)
        ):
            raise ValueError("Некорректный файл проекта")
        relative = item["path"].replace("\\", "/")
        path = Path(relative)
        if path.is_absolute() or path.drive or ".." in path.parts or not path.parts:
            raise ValueError("Файл проекта выходит за его папку")
        if any(
            Path(part).is_reserved()
            or part.endswith((".", " "))
            or any(char in part for char in '<>:"|?*\0')
            for part in path.parts
        ):
            raise ValueError("Недопустимое имя файла проекта")
        if relative.casefold() in names:
            raise ValueError("Повторяющееся имя файла проекта")
        names.add(relative.casefold())
        total += len(item["content"].encode("utf-8"))
        if total > 200000:
            raise ValueError("Проект превысил допустимый размер")
        if path.suffix.casefold() == ".py":
            try:
                ast.parse(item["content"], filename=relative)
            except SyntaxError as error:
                raise ValueError(
                    "Модель создала ошибочный Python в "
                    + relative
                    + ". Уточни запрос и повтори создание."
                ) from error
        if path.suffix.casefold() == ".json":
            json.loads(item["content"])
        files.append({"path": relative, "content": item["content"]})
    return files


def generate(description: str) -> str:
    """Сгенерировать проект во временном каталоге и атомарно опубликовать после проверки."""
    if not 10 <= len(description.strip()) <= 1800:
        raise ValueError("Опиши проект — назначение, основные действия и желаемый язык")
    speech.publish("state", "Готовлю файлы проекта по описанию…")
    raw = asyncio.run(
        local_brain.complete(
            [
                {
                    "role": "system",
                    "content": "Ты создаёшь небольшой рабочий проект по описанию. Верни JSON name, files[{path,content}]. "
                    "Все пути относительные. Включи README с запуском и проверкой. Для веб-приложения предпочти самостоятельный HTML/CSS/JS без CDN. "
                    "Для Python используй стандартную библиотеку, по возможности добавь unittest. Не добавляй команды оболочки, секреты, автозапуск или изменение системных настроек. "
                    "Сделай минимальный завершённый вариант описанного поведения. Пиши читаемый код.",
                },
                {"role": "user", "content": description},
            ],
            SCHEMA,
            max_tokens=1800,
        )
    )
    value = json.loads(raw)
    files = validate(value)
    root = settings.DATA_DIR / "projects"
    root.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex[:12]
    temporary = root / (".building-" + identifier)
    destination = root / ("project-" + identifier)
    temporary.mkdir()
    try:
        for item in files:
            if speech.is_cancelled():
                raise InterruptedError
            path = temporary / item["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item["content"], encoding="utf-8")
        (temporary / "varya-generation.json").write_text(
            json.dumps(
                {
                    "description": description,
                    "files": [item["path"] for item in files],
                    "verification": "Python AST and JSON syntax, execution not performed",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.rename(destination)
    finally:
        if temporary.exists():
            # Каталог создан этой функцией и находится внутри каталога проектов.
            shutil.rmtree(temporary)
    return str(destination)
