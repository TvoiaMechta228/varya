"""План сортировки, проверка файлов и восстановление после перезапуска."""

import json
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from win32com.shell import shell, shellcon

import settings

GROUPS = {
    "Документы": ".pdf .doc .docx .odt .rtf .txt .md .xlsx .xls .csv .pptx".split(),
    "Изображения": ".jpg .jpeg .png .webp .bmp .gif .tiff".split(),
    "Медиа": ".mp3 .flac .wav .mp4 .mkv .mov .avi".split(),
    "Архивы": ".zip .7z .rar .tar .gz".split(),
}
BY_EXT = {ext: group for group, exts in GROUPS.items() for ext in exts}
SORT_DIR = "По типам"


@contextmanager
def database():
    """Закрыть соединение после атомарной записи или чтения."""
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.DATA_DIR / "operations.sqlite3", timeout=4)
    try:
        with db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, stamp TEXT, kind TEXT, detail TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, root TEXT, plan TEXT)"
            )
            yield db
    finally:
        db.close()


def record(kind: str, detail: str) -> None:
    with database() as db:
        db.execute(
            "INSERT INTO events(stamp,kind,detail) VALUES(datetime('now','localtime'),?,?)",
            (kind, detail[:4000]),
        )
        db.execute("DELETE FROM events WHERE id <= (SELECT max(id)-500 FROM events)")


def history(limit: int = 100) -> list:
    with database() as db:
        return db.execute(
            "SELECT stamp,kind,detail FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def desktop_root() -> Path:
    return Path(
        shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOPDIRECTORY, 0, 0)
    ).resolve(strict=True)


def ordinary(path: Path, directory: bool = False) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    forbidden = (
        stat.FILE_ATTRIBUTE_REPARSE_POINT
        | stat.FILE_ATTRIBUTE_HIDDEN
        | stat.FILE_ATTRIBUTE_SYSTEM
    )
    return not info.st_file_attributes & forbidden and (
        stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    )


def signature(path: Path) -> tuple[int, ...]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


@dataclass(frozen=True)
class Move:
    name: str
    group: str
    stamp: tuple[int, ...]


@dataclass(frozen=True)
class Batch:
    id: str
    root: Path
    moves: tuple[Move, ...]


def preview() -> Batch:
    root = desktop_root()
    moves = []
    for path in sorted(root.iterdir(), key=lambda p: p.name.casefold()):
        group = BY_EXT.get(path.suffix.lower())
        if (
            group
            and ordinary(path)
            and not (root / SORT_DIR / group / path.name).exists()
        ):
            try:
                moves.append(Move(path.name, group, signature(path)))
            except OSError:
                continue
        if len(moves) == 80:
            break
    return Batch(uuid.uuid4().hex, root, tuple(moves))


def latest() -> Batch | None:
    root = desktop_root()
    with database() as db:
        row = db.execute(
            "SELECT id,plan FROM batches WHERE root=? ORDER BY rowid DESC LIMIT 1",
            (str(root),),
        ).fetchone()
    if not row:
        return None
    return Batch(
        row[0],
        root,
        tuple(
            Move(name, group, tuple(stamp)) for name, group, stamp in json.loads(row[1])
        ),
    )


def apply(batch: Batch, undo: bool, cancelled) -> tuple[int, int]:
    """Переместить совпавшие объекты. На Windows rename сохраняет существующую цель."""
    if batch.root != desktop_root() or not ordinary(batch.root, directory=True):
        raise OSError("Рабочий стол изменился.")
    if not undo:
        data = [(m.name, m.group, m.stamp) for m in batch.moves]
        with database() as db:
            db.execute(
                "INSERT OR IGNORE INTO batches VALUES(?,?,?)",
                (batch.id, str(batch.root), json.dumps(data, ensure_ascii=False)),
            )
    done = skipped = 0
    for m in batch.moves:
        if cancelled():
            break
        if (
            Path(m.name).name != m.name
            or BY_EXT.get(Path(m.name).suffix.lower()) != m.group
        ):
            skipped += 1
            continue
        base = batch.root / SORT_DIR
        folder = base / m.group
        original, sorted_path = batch.root / m.name, folder / m.name
        src, dst = (sorted_path, original) if undo else (original, sorted_path)
        try:
            for directory in (base, folder):
                if not undo:
                    directory.mkdir(exist_ok=True)
                if not ordinary(directory, directory=True):
                    raise OSError("Каталог изменился")
            if not ordinary(src) or signature(src) != m.stamp or dst.exists():
                skipped += 1
                continue
            if cancelled():
                break
            src.rename(dst)
            done += 1
        except OSError:
            skipped += 1
    if undo and all(
        ordinary(batch.root / m.name) and signature(batch.root / m.name) == m.stamp
        for m in batch.moves
    ):
        with database() as db:
            db.execute("DELETE FROM batches WHERE id=?", (batch.id,))
    return done, skipped
