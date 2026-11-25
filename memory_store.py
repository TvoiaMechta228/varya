"""Долговременные факты и завершённые беседы в локальной SQLite."""

import re

import settings
from workspace_tasks import database


def tables(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, body TEXT NOT NULL, folded TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS exchanges(id INTEGER PRIMARY KEY, request TEXT NOT NULL, answer TEXT NOT NULL)"
    )


def remember(text: str) -> None:
    """Сохранить явно сообщённый факт; ограничить размер и число записей."""
    text = text.strip()
    if not 1 <= len(text) <= 2000:
        raise ValueError("Факт должен содержать от 1 до 2000 символов")
    with database() as db:
        tables(db)
        if not db.execute(
            "SELECT 1 FROM memories WHERE folded=?", (text.casefold(),)
        ).fetchone():
            db.execute(
                "INSERT INTO memories(body,folded) VALUES(?,?)", (text, text.casefold())
            )
        db.execute(
            "DELETE FROM memories WHERE id <= (SELECT max(id)-500 FROM memories)"
        )


def recall(query: str = "") -> str:
    """Вернуть подходящие факты по словам запроса, не отправляя их наружу."""
    words = re.findall(r"[\w]{3,}", query.casefold())[:12]
    with database() as db:
        tables(db)
        rows = db.execute(
            "SELECT body,folded FROM memories ORDER BY id DESC LIMIT 500"
        ).fetchall()
    ranked = sorted(
        rows, key=lambda row: sum(word in row[1] for word in words), reverse=True
    )
    selected = [
        row[0] for row in ranked if not words or any(word in row[1] for word in words)
    ]
    return "\n".join(selected[:12])[:4000]


def exchange(request: str, answer: str) -> None:
    """Сохранить завершённый обмен, если пользователь разрешил историю."""
    if not settings.snapshot()["save_history"]:
        return
    with database() as db:
        tables(db)
        db.execute(
            "INSERT INTO exchanges(request,answer) VALUES(?,?)",
            (request[:2000], answer[:8000]),
        )
        db.execute(
            "DELETE FROM exchanges WHERE id <= (SELECT max(id)-200 FROM exchanges)"
        )


def recent() -> list[dict]:
    """Восстановить последние три обмена при новом запуске разговора."""
    if not settings.snapshot()["save_history"]:
        return []
    with database() as db:
        tables(db)
        rows = db.execute(
            "SELECT request,answer FROM exchanges ORDER BY id DESC LIMIT 3"
        ).fetchall()
    return [
        message
        for request, answer in reversed(rows)
        for message in (
            {"role": "user", "content": request},
            {"role": "assistant", "content": answer},
        )
    ]


def forget(*, conversation_only: bool = False) -> None:
    """Удалить беседу или всю явно подтверждённую память."""
    with database() as db:
        tables(db)
        db.execute("DELETE FROM exchanges")
        if not conversation_only:
            db.execute("DELETE FROM memories")
