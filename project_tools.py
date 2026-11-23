"""Создание небольших автономных приложений и проверка синтаксиса Python."""

import ast
import os
import tempfile
import tokenize
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import settings
from workspace_tasks import ordinary


def check_project(root: Path, cancelled) -> str:
    """Проверить синтаксис файлов в выбранном проекте без импорта его модулей."""
    if not root.is_dir():
        raise OSError("Папка проекта недоступна.")
    problems = []
    checked = ignored = 0
    for directory, folders, files in os.walk(root):
        folders[:] = [
            n
            for n in folders
            if not n.startswith(".")
            and n
            not in {
                "node_modules",
                "build",
                "dist",
                "__pycache__",
                "venv",
                "site-packages",
            }
            and ordinary(Path(directory) / n, directory=True)
        ]
        for name in files:
            if cancelled():
                raise InterruptedError
            path = Path(directory) / name
            if path.suffix.lower() != ".py":
                continue
            if checked >= 300:
                return (
                    summary(checked, ignored, problems)
                    + "\nДостигнут предел в 300 файлов."
                )
            try:
                if not ordinary(path) or path.stat().st_size > 1024 * 1024:
                    ignored += 1
                    continue
                with tokenize.open(path) as source:
                    ast.parse(source.read(), filename=name)
                checked += 1
            except (
                SyntaxError,
                UnicodeError,
                OSError,
                ValueError,
                RecursionError,
            ) as exc:
                checked += 1
                problems.append(
                    f"{path.relative_to(root)} · строка {getattr(exc, 'lineno', '—')} · {exc}"
                )
    return summary(checked, ignored, problems)


def summary(checked: int, ignored: int, problems: list[str]) -> str:
    return "\n".join(
        [
            f"Проверено Python-файлов — {checked}. Ошибок — {len(problems)}. Пропущено — {ignored}.",
            *problems[:30],
        ]
    )


class PageCheck(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = Counter()

    def handle_starttag(self, tag, attrs):
        self.tags[tag] += 1


def create_app(kind: str) -> Path:
    """Собрать локальный HTML-проект из поставляемого шаблона и проверить файл."""
    template = {"expenses": "expenses", "shop": "shop"}[kind]
    content = (Path(__file__).parent / "templates" / (template + ".html")).read_text(
        encoding="utf-8"
    )
    base = settings.DATA_DIR / "projects"
    base.mkdir(parents=True, exist_ok=True)
    project = Path(tempfile.mkdtemp(prefix=template + "-", dir=base))
    entry = project / "index.html"
    with entry.open("x", encoding="utf-8") as output:
        output.write(content)
    parser = PageCheck()
    parser.feed(entry.read_text(encoding="utf-8"))
    if not all(
        parser.tags[tag] for tag in ("html", "title", "main", "output", "script")
    ):
        raise ValueError("Не удалось проверить страницу проекта.")
    return entry


def archive(root: Path, cancelled) -> Path:
    """Упаковать исходные тексты проекта в новый ZIP, пропуская ссылки и окружения."""
    import zipfile
    from uuid import uuid4

    allowed = {".py", ".js", ".ts", ".css", ".html", ".md", ".txt", ".toml"}
    folder = settings.DATA_DIR / "archives"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"project-{uuid4().hex[:12]}.zip"
    size = count = 0
    try:
        with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as output:
            for directory, dirs, names in os.walk(root):
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".")
                    and d
                    not in {"venv", "__pycache__", "build", "dist", "node_modules"}
                    and ordinary(Path(directory) / d, directory=True)
                ]
                for name in names:
                    if cancelled():
                        raise InterruptedError
                    path = Path(directory) / name
                    if (
                        name.startswith(".")
                        or path.suffix.lower() not in allowed
                        or not ordinary(path)
                    ):
                        continue
                    size += path.stat().st_size
                    count += 1
                    if size > 32 * 1024 * 1024 or count > 1000:
                        raise ValueError(
                            "Проект больше 32 МиБ или содержит более 1000 исходных файлов."
                        )
                    output.write(path, path.relative_to(root))
        if not count:
            raise ValueError("В выбранной папке нет подходящих исходных файлов.")
        with zipfile.ZipFile(target) as saved:
            if saved.testzip() is not None:
                raise OSError("Не удалось проверить архив.")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


def test_project(root: Path, cancelled) -> tuple[bool, str]:
    """Запустить pytest в окружении выбранного проекта, ограничив время и дерево процессов."""
    import subprocess
    import time
    from contextlib import ExitStack
    from uuid import uuid4

    import pywintypes
    import win32con
    import win32event
    import win32file
    import win32job
    import win32process

    exe = root / ".venv" / "Scripts" / "python.exe"
    if not exe.is_file():
        raise ValueError("В проекте требуется .venv с Python и pytest.")
    with (
        tempfile.TemporaryDirectory(prefix="VaryaTests-") as directory,
        ExitStack() as handles,
    ):
        capture = Path(directory) / "pytest.txt"
        attr = pywintypes.SECURITY_ATTRIBUTES()
        attr.bInheritHandle = True
        handle = win32file.CreateFile(
            str(capture),
            win32con.GENERIC_WRITE,
            win32con.FILE_SHARE_READ,
            attr,
            win32con.CREATE_NEW,
            0,
            None,
        )
        handles.callback(handle.Close)
        stdin = win32file.CreateFile(
            "NUL",
            win32con.GENERIC_READ,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
            attr,
            win32con.OPEN_EXISTING,
            0,
            None,
        )
        handles.callback(stdin.Close)
        job = win32job.CreateJobObject(None, "VaryaTests" + uuid4().hex)
        proc = thread = None
        try:
            info = win32job.QueryInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation
            )
            info["BasicLimitInformation"]["LimitFlags"] |= (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            win32job.SetInformationJobObject(
                job, win32job.JobObjectExtendedLimitInformation, info
            )
            startup = win32process.STARTUPINFO()
            startup.dwFlags |= win32con.STARTF_USESTDHANDLES
            startup.hStdOutput = startup.hStdError = handle
            startup.hStdInput = stdin
            cmd = subprocess.list2cmdline(
                [str(exe), "-m", "pytest", "-q", "--maxfail=2", "--color=no"]
            )
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            proc, thread, _, _ = win32process.CreateProcess(
                str(exe),
                cmd,
                None,
                None,
                True,
                subprocess.CREATE_NO_WINDOW | win32con.CREATE_SUSPENDED,
                env,
                str(root),
                startup,
            )
            win32job.AssignProcessToJobObject(job, proc)
            if cancelled():
                raise InterruptedError
            win32process.ResumeThread(thread)
            deadline = time.monotonic() + 120
            while win32event.WaitForSingleObject(proc, 150) == win32event.WAIT_TIMEOUT:
                if cancelled():
                    raise InterruptedError
                if time.monotonic() > deadline or capture.stat().st_size > 4_000_000:
                    raise ValueError(
                        "Тесты достигли предела времени или размера вывода."
                    )
            code = win32process.GetExitCodeProcess(proc)
        finally:
            if proc and win32process.GetExitCodeProcess(proc) == 259:
                win32process.TerminateProcess(proc, 1)
            job.Close()
            if thread:
                thread.Close()
            if proc:
                win32event.WaitForSingleObject(proc, 2000)
                proc.Close()
            handles.close()
        return code == 0, capture.read_text(encoding="utf-8", errors="replace")[-20000:]
