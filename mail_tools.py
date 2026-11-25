"""Локальный почтовый профиль, защищённый Windows DPAPI, IMAP и подтверждённый SMTP."""

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import make_msgid, parseaddr
import imaplib
import json
import re
import smtplib
import ssl
import uuid

import settings


def address(value: str) -> str:
    """Проверить один адрес и исключить внедрение почтовых заголовков."""
    if not isinstance(value, str) or any(char in value for char in "\r\n"):
        raise ValueError("Укажи один почтовый адрес")
    parsed = parseaddr(value)[1]
    if parsed != value.strip() or not re.fullmatch(
        r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", parsed
    ):
        raise ValueError("Укажи адрес вида name@example.com")
    return parsed


def profile() -> dict:
    """Расшифровать профиль только для текущего пользователя Windows."""
    import win32crypt

    path = settings.DATA_DIR / "mail-profile.dat"
    if not path.exists():
        raise ValueError(
            "Сначала подключи почту в настройках Вари. Нужны адреса IMAP/SMTP и пароль приложения провайдера."
        )
    try:
        _, raw = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)
        return json.loads(raw.decode("utf-8"))
    except Exception as error:
        raise ValueError(
            "Windows не смогла открыть почтовый профиль. Подключи аккаунт заново."
        ) from error


def configure(data: dict) -> dict:
    """Сохранить конфигурацию в зашифрованном виде без проверки пароля через сеть."""
    import win32crypt

    checked = {"address": address(data.get("address", ""))}
    for key in ("imap", "smtp"):
        host = data.get(key, "").strip()
        if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host) or host.startswith("."):
            raise ValueError(
                "Укажи имя сервера " + key.upper() + " из настроек провайдера"
            )
        checked[key] = host
    for key, default in (("imap_port", 993), ("smtp_port", 465)):
        value = int(data.get(key, default))
        if not 1 <= value <= 65535:
            raise ValueError("Некорректный номер порта")
        checked[key] = value
    password = data.get("password", "")
    if not isinstance(password, str) or not password:
        old = profile()
        if old["address"] != checked["address"]:
            raise ValueError("Для нового аккаунта нужен пароль приложения")
        password = old["password"]
    checked["password"] = password
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    encrypted = win32crypt.CryptProtectData(
        json.dumps(checked).encode("utf-8"), "Varya mail", None, None, None, 0
    )
    temporary = settings.DATA_DIR / "mail-profile.tmp"
    temporary.write_bytes(encrypted)
    temporary.replace(settings.DATA_DIR / "mail-profile.dat")
    return {key: value for key, value in checked.items() if key != "password"}


def unread() -> str:
    """Прочитать до десяти непрочитанных писем, сохраняя флаг непрочитанного."""
    account = profile()
    context = ssl.create_default_context()
    with imaplib.IMAP4_SSL(
        account["imap"], account["imap_port"], ssl_context=context, timeout=20
    ) as client:
        client.login(account["address"], account["password"])
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise OSError("Не удалось открыть входящие")
        status, result = client.uid("search", None, "UNSEEN")
        if status != "OK":
            raise OSError("Не удалось получить список непрочитанных")
        ids = result[0].split()[-10:]
        letters = []
        for identifier in reversed(ids):
            status, chunks = client.uid("fetch", identifier, "(BODY.PEEK[]<0.100000>)")
            if status != "OK":
                continue
            raw = b"".join(
                item[1]
                for item in chunks
                if isinstance(item, tuple) and isinstance(item[1], bytes)
            )
            message = BytesParser(policy=policy.default).parsebytes(raw)
            body = message.get_body(preferencelist=("plain",))
            text = body.get_content() if body else "Текстовая часть отсутствует."
            letters.append(
                f"От — {message.get('From', '')}\nТема — {message.get('Subject', '')}\n{str(text)[:4000]}"
            )
        return "\n\n".join(letters)[:20000] or "Непрочитанных писем нет."


def message(data: dict) -> EmailMessage:
    """Составить письмо из явно показанных пользователю данных."""
    result = EmailMessage()
    result["To"] = address(data["to"])
    subject = data["subject"]
    if (
        not isinstance(subject, str)
        or len(subject) > 200
        or any(char in subject for char in "\r\n")
    ):
        raise ValueError("Тема должна быть одной строкой до 200 символов")
    if not isinstance(data["body"], str) or not 1 <= len(data["body"]) <= 20000:
        raise ValueError("Укажи текст письма до 20000 символов")
    result["Subject"] = subject
    result["Message-ID"] = data.setdefault("message_id", make_msgid())
    result.set_content(data["body"])
    return result


def draft(data: dict) -> str:
    """Создать проверяемый EML-черновик для любого почтового клиента."""
    output = message(data)
    folder = settings.DATA_DIR / "mail-drafts"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (uuid.uuid4().hex + ".eml")
    with path.open("xb") as stream:
        stream.write(output.as_bytes(policy=policy.SMTP))
    return str(path)


def send(data: dict) -> str:
    """Отправить подтверждённое письмо один раз. Не повторять после неясного сетевого сбоя."""
    output = message(data)
    account = profile()
    if account["address"] != data.get("sender"):
        raise ValueError("Отправляющий аккаунт изменился. Запроси отправку заново.")
    output["From"] = account["address"]
    submitted = False
    try:
        context = ssl.create_default_context()
        if account["smtp_port"] == 465:
            client = smtplib.SMTP_SSL(
                account["smtp"], account["smtp_port"], timeout=25, context=context
            )
        else:
            client = smtplib.SMTP(account["smtp"], account["smtp_port"], timeout=25)
        with client:
            if account["smtp_port"] != 465:
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            client.login(account["address"], account["password"])
            submitted = True
            rejected = client.send_message(output)
            if rejected:
                raise OSError("Сервер отклонил получателя")
        return (
            "Почтовый сервер принял письмо для "
            + data["to"]
            + ". Идентификатор — "
            + str(output["Message-ID"])
        )
    except (OSError, smtplib.SMTPException) as error:
        if submitted:
            raise OSError(
                "Статус отправки не подтверждён. Проверь папку отправленных перед повтором, чтобы избежать дубля."
            ) from error
        raise OSError(
            "Не удалось подключиться к почтовому серверу или войти в аккаунт"
        ) from error
