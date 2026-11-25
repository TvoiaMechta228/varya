"""Проверяемые операции над текстом, DOCX, XLSX, PPTX и изображениями."""

import os
from pathlib import Path
import re
import shutil
import uuid

import settings


def file_path(value: str, *, existing: bool = True) -> Path:
    """Разрешить явный путь с проверкой существования и размера файла."""
    path = Path(os.path.expandvars(value.strip().strip('"«»'))).expanduser()
    if not path.is_absolute():
        path = Path.home() / "Documents" / path
    path = path.resolve(strict=existing)
    if existing and (not path.is_file() or path.stat().st_size > 50 * 1024 * 1024):
        raise ValueError("Нужен файл размером до 50 МиБ")
    if not existing and not path.parent.is_dir():
        raise ValueError("Папка назначения не существует")
    return path


def signature(path: Path) -> tuple | None:
    if not path.exists():
        return None
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def read(value: str) -> str:
    """Прочитать содержимое документа, сохранив координаты таблиц и слайдов."""
    path = file_path(value)
    ext = path.suffix.casefold()
    if ext in {".txt", ".md", ".csv", ".json", ".py", ".html", ".log"}:
        try:
            return path.read_text(encoding="utf-8-sig")[:16000]
        except UnicodeDecodeError:
            return path.read_text(encoding="cp1251")[:16000]
    if ext == ".docx":
        from docx import Document

        doc = Document(path)
        rows = [p.text for p in doc.paragraphs]
        rows += [
            " | ".join(cell.text for cell in row.cells)
            for table in doc.tables
            for row in table.rows
        ]
        return "\n".join(rows)[:16000]
    if ext in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook

        book = load_workbook(path, read_only=True, data_only=False)
        try:
            lines = []
            for sheet in book:
                lines.append("Лист «" + sheet.title + "»")
                for row in sheet.iter_rows(
                    max_row=min(sheet.max_row, 200), max_col=min(sheet.max_column, 30)
                ):
                    lines.append(
                        " | ".join(
                            f"{cell.coordinate}={cell.value}"
                            for cell in row
                            if cell.value is not None
                        )
                    )
            return "\n".join(lines)[:16000]
        finally:
            book.close()
    if ext == ".pptx":
        from pptx import Presentation

        return "\n".join(
            f"Слайд {i}\n"
            + "\n".join(shape.text for shape in slide.shapes if shape.has_text_frame)
            for i, slide in enumerate(Presentation(path).slides, 1)
        )[:16000]
    if ext == ".pdf":
        from pypdf import PdfReader

        return "\n".join(
            page.extract_text() or "" for page in PdfReader(path).pages[:30]
        )[:16000]
    raise ValueError("Чтение поддерживает TXT, DOCX, XLSX, PPTX и текстовый PDF")


def commit_file(
    temporary: Path, destination: Path, expected: tuple | None
) -> Path | None:
    """Проверить исходный файл повторно, сохранить резервную копию и заменить результат."""
    if signature(destination) != expected:
        raise ValueError("Файл изменился после подтверждения. Запроси операцию заново.")
    backup = None
    if destination.exists():
        folder = settings.DATA_DIR / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / (uuid.uuid4().hex + destination.suffix)
        shutil.copy2(destination, backup)
        os.replace(temporary, destination)
    else:
        # Windows rename не заменяет файл, появившийся после проверки.
        temporary.rename(destination)
    return backup


def write(action: str, data: dict, expected: tuple | None) -> str:
    """Создать или изменить документ через временный файл и проверку прочитанного результата."""
    path = file_path(data["path"], existing=False)
    if signature(path) != expected:
        raise ValueError("Файл изменился. Повтори просьбу.")
    temporary = path.with_name(".varya-" + uuid.uuid4().hex + path.suffix)
    try:
        if action == "write_text":
            temporary.write_text(data["text"], encoding="utf-8")
        elif action == "word_append":
            from docx import Document

            if path.suffix.casefold() != ".docx":
                raise ValueError("Укажи документ DOCX")
            doc = Document(path) if path.exists() else Document()
            for line in data["text"].splitlines():
                doc.add_paragraph(line)
            doc.save(temporary)
            if not any(
                p.text == data["text"].splitlines()[-1]
                for p in Document(temporary).paragraphs
            ):
                raise OSError("Не удалось проверить сохранённый текст")
        elif action == "excel_set":
            from openpyxl import Workbook, load_workbook

            if path.suffix.casefold() != ".xlsx" or not re.fullmatch(
                r"[A-Z]{1,3}[1-9][0-9]{0,6}", data["cell"]
            ):
                raise ValueError("Нужны файл XLSX и адрес ячейки, например B2")
            book = load_workbook(path) if path.exists() else Workbook()
            try:
                sheet_name = data.get("sheet") or book.active.title
                sheet = (
                    book[sheet_name]
                    if sheet_name in book.sheetnames
                    else book.create_sheet(sheet_name)
                )
                sheet[data["cell"]] = data["value"]
                book.save(temporary)
            finally:
                book.close()
            check = load_workbook(temporary, read_only=True)
            try:
                if check[sheet_name][data["cell"]].value != data["value"]:
                    raise OSError("Значение ячейки не сохранилось")
            finally:
                check.close()
        elif action == "slide_add":
            from pptx import Presentation

            if path.suffix.casefold() != ".pptx":
                raise ValueError("Нужна презентация PPTX")
            deck = Presentation(path) if path.exists() else Presentation()
            slide = deck.slides.add_slide(deck.slide_layouts[1])
            slide.shapes.title.text = data["title"]
            slide.placeholders[1].text = data["text"]
            deck.save(temporary)
            if Presentation(temporary).slides[-1].shapes.title.text != data["title"]:
                raise OSError("Слайд не сохранился")
        elif action == "image_edit":
            from PIL import Image, ImageOps

            source = file_path(data["source"])
            with Image.open(source) as original:
                image = ImageOps.exif_transpose(original)
                if "width" in data:
                    width, height = int(data["width"]), int(data["height"])
                    if (
                        not 1 <= width <= 16000
                        or not 1 <= height <= 16000
                        or width * height > 40000000
                    ):
                        raise ValueError(
                            "Укажи размеры до 16000 пикселей, всего до 40 мегапикселей"
                        )
                    image = image.resize((width, height), Image.Resampling.LANCZOS)
                if "rotate" in data:
                    image = image.rotate(int(data["rotate"]) % 360, expand=True)
                if path.suffix.casefold() in {".jpg", ".jpeg"}:
                    image = image.convert("RGB")
                image.save(temporary)
            with Image.open(temporary) as check:
                check.verify()
        else:
            raise ValueError("Неизвестная операция документа")
        backup = commit_file(temporary, path, expected)
        return f"Сохранён файл {path}." + (
            f" Резервная копия — {backup}." if backup else ""
        )
    finally:
        temporary.unlink(missing_ok=True)
