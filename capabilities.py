"""Единый вход новых инструментов, проверка параметров и подтверждение изменений."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import time

from command_results import Result
import document_tools as documents
import settings
import speech

DESCRIPTIONS = {
    "yandex_command": "команда сервисам Яндекса; argument — вся просьба пользователя: открыть Яндекс Музыку, искать в Картах/Маркете/Музыке, маршрут, перевод, медиакоманды. Не заменять Яндекс Музыку локальными файлами",
    "browser_open": "открыть сайт в управляемом браузере; argument — http/https адрес",
    "browser_tabs": "перечислить вкладки управляемого браузера",
    "browser_select": "выбрать вкладку; argument — её номер",
    "browser_read": "прочитать текущую страницу управляемого браузера",
    "browser_elements": "показать подписи кнопок, ссылок и полей текущего сайта",
    "browser_click": "нажать кнопку сайта; argument — точная подпись",
    "browser_fill": 'заполнить поле сайта; argument — JSON {"name":"подпись поля","text":"значение"}',
    "file_search": "найти файлы в пользовательских папках; argument — часть имени",
    "file_open": "открыть найденный файл; argument — номер из последнего списка или абсолютный путь",
    "file_read": "прочитать TXT, DOCX, XLSX, PPTX или PDF; argument — путь или номер найденного файла",
    "file_copy": 'копировать файл; argument — JSON {"source":"путь","path":"полный путь назначения"}',
    "file_move": 'переместить или переименовать файл; argument — JSON {"source":"путь","path":"полный путь назначения"}',
    "file_recycle": "переместить файл в корзину; argument — полный путь или номер найденного файла",
    "write_text": 'сохранить текст; argument — JSON {"path":"файл.txt","text":"содержимое"}',
    "word_append": 'создать DOCX или добавить абзац; argument — JSON {"path":"файл.docx","text":"текст"}',
    "excel_set": 'задать значение Excel; argument — JSON {"path":"файл.xlsx","cell":"A1","value":"значение","sheet":"Лист1"}',
    "slide_add": 'добавить слайд PPTX; argument — JSON {"path":"файл.pptx","title":"заголовок","text":"содержимое"}',
    "image_edit": 'изменить размер или повернуть изображение; argument — JSON {"source":"исходник.png","path":"результат.png","width":800,"height":600} или rotate вместо размеров',
    "profile_remember": "сохранить долговременный факт или предпочтение; argument — только явно сообщённый факт",
    "profile_recall": "найти сохранённые факты и предпочтения; argument — вопрос или пустая строка для всех",
    "profile_forget": "удалить сохранённую память о пользователе после подтверждения",
    "mail_unread": "показать непрочитанные письма подключённого IMAP-аккаунта",
    "mail_draft": 'создать EML-черновик; argument — JSON {"to":"адрес","subject":"тема","body":"текст"}',
    "mail_send": 'предложить отправку письма; argument — JSON {"to":"адрес","subject":"тема","body":"текст"}',
    "music_play": "воспроизвести локальную музыку; argument — часть имени трека или пустая строка для всей папки",
    "music_pause": "поставить встроенный музыкальный плеер на паузу",
    "music_resume": "продолжить воспроизведение локальной музыки",
    "music_next": "следующий трек встроенного плеера",
    "music_previous": "предыдущий трек встроенного плеера",
    "music_status": "показать название текущего трека",
    "window_fields": "показать поля окна и их содержимое; argument — часть названия окна",
    "window_fill": 'записать поле окна; argument — JSON {"window":"название окна","field":"номер или подпись поля","text":"значение"}',
    "internet_answer": "найти ответ в интернете по реальным страницам; argument — вопрос",
    "project_generate": "сгенерировать новый небольшой проект по описанию; argument — назначение и требования",
    "plan_create": "составить и выполнить многошаговый план; argument — полное поручение пользователя",
    "plan_resume": "продолжить сохранённый незавершённый план после просмотра; argument — идентификатор плана",
}
READ_ONLY = {
    "browser_tabs",
    "browser_read",
    "browser_elements",
    "file_search",
    "file_read",
    "profile_recall",
    "mail_unread",
}
IMMEDIATE = READ_ONLY | {
    "yandex_command",
    "browser_open",
    "browser_select",
    "file_open",
    "profile_remember",
}
IMMEDIATE.update(key for key in DESCRIPTIONS if key.startswith("music_"))
READ_ONLY.add("music_status")
READ_ONLY.add("window_fields")
IMMEDIATE.add("window_fields")
IMMEDIATE.add("internet_answer")
DOCUMENT_ACTIONS = {"write_text", "word_append", "excel_set", "slide_add", "image_edit"}


@dataclass(frozen=True)
class Pending:
    action: str
    data: dict
    expires: float
    signatures: tuple[tuple[str, tuple | None], ...] = ()


def selected_path(session, argument: str, *, existing: bool = True) -> Path:
    if argument.casefold().strip() in {
        "его",
        "этот файл",
        "этот документ",
        "выбранный файл",
    }:
        if not session.selected_file:
            raise ValueError("Сначала выбери файл по пути или номеру из поиска")
        argument = session.selected_file
    if argument.strip().isdigit():
        index = int(argument.strip()) - 1
        if not 0 <= index < len(session.file_choices):
            raise ValueError("Укажи номер из последнего списка файлов")
        argument = session.file_choices[index]
    return documents.file_path(argument, existing=existing)


def parameters(session, action: str, argument: str) -> dict:
    """Разобрать данные инструмента без выполнения изменений."""
    if action not in DESCRIPTIONS:
        raise ValueError("Неизвестный инструмент")
    if action in {"plan_create", "plan_resume"}:
        import planning

        return planning.prepare(argument, resume=action == "plan_resume")
    if action == "window_fill":
        import window_fields

        data = json.loads(argument)
        if not isinstance(data, dict) or not {"field", "text"} <= data.keys():
            raise ValueError("Укажи поле и текст")
        return window_fields.prepare(session, data)
    if action in {"mail_draft", "mail_send"}:
        import mail_tools

        try:
            data = json.loads(argument)
        except ValueError as error:
            raise ValueError("Укажи получателя, тему и текст письма") from error
        if not isinstance(data, dict) or not {"to", "subject", "body"} <= data.keys():
            raise ValueError("Нужны получатель, тема и текст письма")
        mail_tools.message(data)
        if action == "mail_send":
            data["sender"] = mail_tools.profile()["address"]
        return data
    if action in DOCUMENT_ACTIONS | {"file_copy", "file_move", "browser_fill"}:
        try:
            data = json.loads(argument)
        except (ValueError, TypeError) as error:
            raise ValueError("Нужно уточнить параметры файла или поля") from error
        if not isinstance(data, dict):
            raise ValueError("Ожидались параметры операции")
        required = {
            "write_text": {"path", "text"},
            "word_append": {"path", "text"},
            "excel_set": {"path", "cell", "value"},
            "slide_add": {"path", "title", "text"},
            "image_edit": {"source", "path"},
            "file_copy": {"source", "path"},
            "file_move": {"source", "path"},
            "browser_fill": {"name", "text"},
        }[action]
        if not required <= data.keys():
            raise ValueError("Уточни параметры — " + ", ".join(sorted(required)))
        if any(not isinstance(data[key], str) for key in required - {"value"}):
            raise ValueError("Путь, подпись и текст должны быть строками")
        if "text" in data and not data["text"].strip():
            raise ValueError("Укажи текст для записи")
        if "path" in data:
            data["path"] = str(selected_path(session, data["path"], existing=False))
        if "source" in data:
            data["source"] = str(selected_path(session, data["source"]))
        if action in {"file_copy", "file_move"} and Path(data["path"]).is_dir():
            data["path"] = str(Path(data["path"]) / Path(data["source"]).name)
        if action in {"file_copy", "file_move"} and Path(data["path"]).exists():
            raise ValueError(
                "В папке назначения уже есть такой файл. Укажи другое имя."
            )
        if action == "image_edit" and not (
            {"width", "height"} <= data.keys() or "rotate" in data
        ):
            raise ValueError("Укажи ширину и высоту или угол поворота")
        if action == "browser_fill":
            import browser_tools

            browser_tools.target("field", data["name"])
            data["url"] = browser_tools.current_url()
            data["_fingerprint"] = browser_tools.fingerprint()
        return data
    if action in {"file_open", "file_read", "file_recycle"}:
        return {"path": str(selected_path(session, argument))}
    if action == "browser_click":
        import browser_tools

        argument = re.sub(r"^кнопку\s+", "", argument, flags=re.I).strip(' «»"')
        browser_tools.target("button", argument)
        return {
            "name": argument,
            "url": browser_tools.current_url(),
            "_fingerprint": browser_tools.fingerprint(),
        }
    return {"argument": argument}


def request(session, action: str, argument: str = "") -> Result:
    """Выполнить чтение или показать точный план изменения пользователю."""
    import missions

    if missions.mode() == "Наблюдатель" and action not in READ_ONLY:
        return Result("Для этого действия выбери режим «Помощник» в настройках.")
    data = parameters(session, action, argument)
    if action in {"plan_create", "plan_resume"}:
        session.capability = Pending(action, data, time.monotonic() + 300)
        speech.publish(
            "private_text",
            "\n".join(
                f"{i}. {item['action']} — {str(item['argument'])[:500]}"
                for i, item in enumerate(data["steps"], 1)
            ),
        )
        return Result(
            f"План из {len(data['steps'])} шагов показан в диалоге. Начать выполнение? Изменения и отправка писем будут подтверждаться отдельно."
        )
    if action in IMMEDIATE:
        return perform(session, action, data)
    signatures = tuple(
        (data[key], documents.signature(Path(data[key])))
        for key in ("path", "source")
        if key in data
    )
    session.capability = Pending(action, data, time.monotonic() + 90, signatures)
    labels = {
        "file_copy": "Копировать файл",
        "file_move": "Переместить файл",
        "file_recycle": "Переместить в корзину",
        "write_text": "Сохранить текст",
        "word_append": "Записать документ Word",
        "excel_set": "Изменить ячейку Excel",
        "slide_add": "Добавить слайд",
        "image_edit": "Изменить изображение",
        "browser_click": "Нажать кнопку сайта",
        "browser_fill": "Заполнить поле сайта",
        "profile_forget": "Удалить долговременную память",
        "mail_draft": "Создать почтовый черновик",
        "mail_send": "Отправить письмо",
        "window_fill": "Записать текст в поле окна",
        "project_generate": "Создать проект по описанию",
    }
    names = {
        "path": "Файл назначения",
        "source": "Исходный файл",
        "name": "Элемент",
        "text": "Текст",
        "url": "Страница",
        "to": "Получатель",
        "subject": "Тема",
        "body": "Текст письма",
        "sender": "Отправитель",
        "cell": "Ячейка",
        "value": "Значение",
        "sheet": "Лист",
        "title": "Заголовок",
        "width": "Ширина",
        "height": "Высота",
        "rotate": "Поворот",
    }
    detail = "\n".join(
        f"{names.get(key, key)} — {str(value)[:600]}"
        for key, value in data.items()
        if key != "argument" and key != "message_id" and not key.startswith("_")
    )
    return Result(
        labels[action]
        + ("\n" + detail if detail else "")
        + "\nПодтверждаешь? Да или нет."
    )


def confirmation(session, command: str) -> Result | None:
    pending, session.capability = session.capability, None
    if pending is None:
        return None
    if command in {"нет", "отмена", "отмени", "не надо"}:
        return Result("Отменила.", ok=False)
    if command not in {"да", "подтверждаю", "сделай"}:
        return None
    if time.monotonic() > pending.expires or speech.is_cancelled():
        return Result("Подтверждение истекло. Повтори просьбу.", ok=False)
    if any(
        documents.signature(Path(path)) != stamp for path, stamp in pending.signatures
    ):
        return Result(
            "Файл изменился после предложения. Повтори просьбу для нового подтверждения.",
            ok=False,
        )
    return perform(session, pending.action, pending.data, dict(pending.signatures))


def search_files(session, query: str) -> Result:
    """Искать по пользовательским каталогам с пределом обхода и поддержкой остановки."""
    if not query.strip() or len(query) > 120:
        raise ValueError("Назови часть имени файла")
    roots = [
        Path.home() / name
        for name in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")
    ]
    project = settings.snapshot().get("project")
    if project:
        roots.insert(0, Path(project))
    found = []
    visited = 0
    seen = set()
    for root in roots:
        for directory, folders, names in os.walk(root, followlinks=False):
            folders[:] = [
                name
                for name in folders
                if name not in {".git", ".venv", "node_modules", "__pycache__"}
                and not (Path(directory) / name).is_symlink()
            ]
            for name in names:
                visited += 1
                if speech.is_cancelled():
                    raise InterruptedError
                if query.casefold() in name.casefold():
                    path = str(Path(directory) / name)
                    if path not in seen:
                        found.append(path)
                        seen.add(path)
                if len(found) >= 40 or visited >= 100000:
                    break
            if len(found) >= 40 or visited >= 100000:
                break
        if len(found) >= 40 or visited >= 100000:
            break
    session.file_choices = tuple(found)
    return Result(
        "\n".join(f"{i}. {path}" for i, path in enumerate(found, 1))
        or "Файлы с таким именем в пользовательских папках не найдены."
    )


def perform(session, action: str, data: dict, stamps: dict | None = None) -> Result:
    """Запустить проверенный инструмент и вернуть наблюдаемый результат."""
    if speech.is_cancelled():
        raise InterruptedError
    argument = data.get("argument", "")
    if action == "yandex_command":
        import yandex_tools

        return yandex_tools.handle(session, argument) or Result(
            "Уточни сервис и действие. Например, «найди в Яндекс Музыке Кино».",
            ok=False,
        )
    if action in {"plan_create", "plan_resume"}:
        import planning

        return planning.start(session, data)
    if action == "internet_answer":
        import internet_tools

        speech.publish("private_text", internet_tools.answer(argument))
        return Result("Ответ и адреса прочитанных источников показаны в диалоге.")
    if action == "project_generate":
        import project_generation

        directory = project_generation.generate(argument)
        speech.publish("artifact_card", directory)
        return Result(
            "Проект сохранён в "
            + directory
            + ". Синтаксис Python и JSON проверен. Запуск сгенерированного кода требует отдельной проверки."
        )
    if action.startswith("window_"):
        import window_fields

        if action == "window_fields":
            speech.publish("private_text", window_fields.describe(session, argument))
            return Result("Показала поля выбранного окна.")
        return Result(window_fields.fill(data))
    if action.startswith("music_"):
        import music_tools

        if action == "music_play":
            session.yandex_service, session.yandex_until = "", 0.0
        argument = re.sub(r"(?:^|\s)пожалуйста$", "", argument, flags=re.I).strip()
        return Result(
            music_tools.play(argument)
            if action == "music_play"
            else music_tools.control(action.removeprefix("music_"))
        )
    if action.startswith("mail_"):
        import mail_tools

        if action == "mail_unread":
            speech.publish("private_text", mail_tools.unread())
            return Result("Показала непрочитанные письма подключённого аккаунта.")
        if action == "mail_draft":
            return Result(
                "Черновик EML создан.", "artifact_card", mail_tools.draft(data)
            )
        return Result(mail_tools.send(data))
    if action.startswith("browser_"):
        import browser_tools as browser

        if action == "browser_open":
            return Result(browser.open_url(argument))
        if action == "browser_tabs":
            return Result(browser.tabs())
        if action == "browser_select":
            return Result(browser.select_tab(int(argument)))
        if action == "browser_elements":
            speech.publish("private_text", browser.elements())
            return Result("Показала подписи элементов текущей страницы.")
        if action == "browser_read":
            speech.publish("private_text", browser.read_page())
            return Result("Текст страницы показан вместе с адресом источника.")
        return Result(browser.interact(action, data))
    if action == "file_search":
        return search_files(session, argument)
    if action == "file_read":
        session.selected_file = data["path"]
        speech.publish("private_text", documents.read(data["path"]))
        return Result("Прочитала файл. Содержимое показано в диалоге.")
    if action == "file_open":
        if Path(data["path"]).suffix.casefold() not in {
            ".txt",
            ".md",
            ".csv",
            ".json",
            ".log",
            ".pdf",
            ".docx",
            ".xlsx",
            ".pptx",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".bmp",
            ".gif",
            ".mp3",
            ".wav",
            ".flac",
            ".mp4",
            ".eml",
        }:
            raise ValueError(
                "Открываю документы, изображения и медиа. Программы запускаются через список разрешённых приложений."
            )
        session.selected_file = data["path"]
        os.startfile(data["path"])
        return Result(
            "Передала Windows запрос на открытие файла «"
            + Path(data["path"]).name
            + "»."
        )
    if action in DOCUMENT_ACTIONS:
        expected = (stamps or {}).get(data["path"])
        return Result(
            documents.write(action, data, expected), "artifact_card", data["path"]
        )
    if action in {"file_copy", "file_move"}:
        source, destination = Path(data["source"]), Path(data["path"])
        if destination.exists():
            raise ValueError("В месте назначения появился файл. Нужен новый запрос.")
        if action == "file_copy":
            with source.open("rb") as origin, destination.open("xb") as output:
                try:
                    shutil.copyfileobj(origin, output)
                except Exception:
                    output.close()
                    destination.unlink(missing_ok=True)
                    raise
        else:
            # rename не заменяет файл на Windows и сохраняет исходник при ошибке тома.
            source.rename(destination)
        if not destination.is_file():
            raise OSError("Результат операции не найден")
        return Result(
            "Готово. Файл — " + str(destination), "artifact_card", str(destination)
        )
    if action == "file_recycle":
        from win32com.shell import shell, shellcon

        result, aborted = shell.SHFileOperation(
            (
                0,
                shellcon.FO_DELETE,
                data["path"] + "\0",
                None,
                shellcon.FOF_ALLOWUNDO
                | shellcon.FOF_NOCONFIRMATION
                | shellcon.FOF_SILENT,
                None,
                None,
            )
        )
        if result or aborted or Path(data["path"]).exists():
            raise OSError("Windows не подтвердила перемещение файла в корзину")
        return Result("Файл перемещён в корзину.")
    if action.startswith("profile_"):
        import memory_store

        if action == "profile_remember":
            memory_store.remember(argument)
            return Result("Сохранила этот факт в долговременной памяти.")
        if action == "profile_forget":
            memory_store.forget()
            return Result("Долговременная память очищена.")
        return Result(memory_store.recall(argument) or "Сохранённых фактов не найдено.")
    raise ValueError("Инструмент не реализован")


def direct(session, raw: str) -> Result | None:
    """Распознать обычные просьбы без загрузки языковой модели."""
    import yandex_tools

    result = yandex_tools.handle(session, raw)
    if result is not None:
        return result
    patterns = (
        (r"(?:включи|запусти|поставь) локальную музыку(?: (.+))?", "music_play"),
        (r"(?:поставь музыку на паузу|пауза музыки)", "music_pause"),
        (
            r"(?:включи|запусти|поставь|хочу послушать|давай послушаем) музыку(?: (.+))?",
            "music_play",
        ),
        (r"(?:продолжи музыку|продолжи воспроизведение)", "music_resume"),
        (r"следующий трек", "music_next"),
        (r"предыдущий трек", "music_previous"),
        (r"что (?:сейчас )?играет", "music_status"),
        (r"(?:открой сайт|перейди на сайт) (.+)", "browser_open"),
        (r"(?:покажи |список )?вкладки браузера", "browser_tabs"),
        (r"(?:выбери|открой) вкладку (\d+)", "browser_select"),
        (r"(?:прочитай страницу|что написано на сайте)", "browser_read"),
        (r"покажи (?:кнопки|поля) (?:сайта|страницы)", "browser_elements"),
        (r"(?:на сайте|в браузере) нажми (.+)", "browser_click"),
        (r"найди файл (.+)", "file_search"),
        (r"открой найденный файл (\d+)", "file_open"),
        (r"прочитай файл (.+)", "file_read"),
        (r"(?:удали файл|убери в корзину файл) (.+)", "file_recycle"),
        (r"запомни обо мне (.+)", "profile_remember"),
        (r"что ты помнишь обо мне", "profile_recall"),
        (r"(?:удали|очисти) память обо мне", "profile_forget"),
        (r"покажи непрочитанные письма моей почты", "mail_unread"),
        (r"покажи поля окна (.+)", "window_fields"),
        (r"(?:найди ответ в интернете|узнай в интернете) (.+)", "internet_answer"),
        (r"(?:разработай проект|создай проект по описанию) (.+)", "project_generate"),
        (
            r"(?:составь и выполни план|выполни поручение|сделай по шагам) (.+)",
            "plan_create",
        ),
        (r"продолжи план (.+)", "plan_resume"),
    )
    if re.fullmatch(
        r"(?:покажи|прочитай) непрочитанные письма", raw.strip().rstrip(".!?"), re.I
    ):
        if (
            os.environ.get("VARYA_NATIVE") == "1"
            or (settings.DATA_DIR / "mail-profile.dat").exists()
        ):
            return request(session, "mail_unread")
    for pattern, action in patterns:
        match = re.fullmatch(pattern, raw.strip().rstrip(".!?"), re.I)
        if match:
            return request(session, action, (match[1] or "") if match.lastindex else "")
    word = re.fullmatch(
        r'(?:запиши|добавь|сохрани|напиши) (?:текст )?в (?:документ |ворд |файл )?[«"]?(.+?\.docx)[»"]?\s+(?:что |следующий текст[: ]+)?(.+)',
        raw.strip(),
        re.I | re.S,
    )
    if word:
        return request(
            session, "word_append", json.dumps({"path": word[1], "text": word[2]})
        )
    quoted = re.fullmatch(
        r'(?:запиши|добавь) в (?:документ|ворд) [«"](.+?)[»"] (.+)', raw, re.I | re.S
    )
    if quoted:
        return request(
            session, "word_append", json.dumps({"path": quoted[1], "text": quoted[2]})
        )
    fill = re.fullmatch(r'в браузере заполни поле [«"](.+?)[»"] (.+)', raw, re.I | re.S)
    if fill:
        return request(
            session, "browser_fill", json.dumps({"name": fill[1], "text": fill[2]})
        )
    field = re.fullmatch(r"в поле (\d+) (?:введи|запиши) (.+)", raw, re.I | re.S)
    if field:
        return request(
            session, "window_fill", json.dumps({"field": field[1], "text": field[2]})
        )
    copy = re.fullmatch(
        r'(скопируй|перемести|переименуй) файл [«"](.+?)[»"] (?:в|как) [«"](.+?)[»"]',
        raw,
        re.I,
    )
    if copy:
        action = "file_copy" if copy[1].casefold() == "скопируй" else "file_move"
        return request(
            session, action, json.dumps({"source": copy[2], "path": copy[3]})
        )
    text_file = re.fullmatch(
        r'сохрани в файл [«"](.+?)[»"] (?:текст )?(.+)', raw, re.I | re.S
    )
    if text_file:
        return request(
            session,
            "write_text",
            json.dumps({"path": text_file[1], "text": text_file[2]}),
        )
    cell = re.fullmatch(
        r'в таблице [«"](.+?)[»"] (?:запиши|поставь) в ([a-z]{1,3}[1-9][0-9]*) (.+)',
        raw,
        re.I,
    )
    if cell:
        return request(
            session,
            "excel_set",
            json.dumps({"path": cell[1], "cell": cell[2].upper(), "value": cell[3]}),
        )
    slide = re.fullmatch(
        r'добавь в презентацию [«"](.+?)[»"] слайд [«"](.+?)[»"] с текстом (.+)',
        raw,
        re.I | re.S,
    )
    if slide:
        return request(
            session,
            "slide_add",
            json.dumps({"path": slide[1], "title": slide[2], "text": slide[3]}),
        )
    image = re.fullmatch(
        r'измени размер изображения [«"](.+?)[»"] до (\d+) на (\d+) и сохрани в [«"](.+?)[»"]',
        raw,
        re.I,
    )
    if image:
        return request(
            session,
            "image_edit",
            json.dumps(
                {
                    "source": image[1],
                    "width": int(image[2]),
                    "height": int(image[3]),
                    "path": image[4],
                }
            ),
        )
    return None
