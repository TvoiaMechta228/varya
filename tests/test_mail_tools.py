"""Почтовые данные защищены, отправка не выполняется до подтверждения."""

import json
from unittest.mock import MagicMock

import pytest

import capabilities
from dialogue import Dialogue
import mail_tools
import settings


def test_profile_is_encrypted_and_password_not_returned():
    result = mail_tools.configure(
        {
            "address": "test@example.com",
            "imap": "imap.example.com",
            "smtp": "smtp.example.com",
            "password": "test-app-password",
        }
    )
    assert "password" not in result
    assert (
        b"test-app-password"
        not in (settings.DATA_DIR / "mail-profile.dat").read_bytes()
    )
    assert mail_tools.profile()["password"] == "test-app-password"


def test_send_requires_explicit_confirmation(monkeypatch):
    monkeypatch.setattr(
        mail_tools,
        "profile",
        lambda: {
            "address": "from@example.com",
            "smtp": "smtp.example.com",
            "smtp_port": 465,
            "password": "secret",
        },
    )
    client = MagicMock()
    client.__enter__.return_value = client
    client.send_message.return_value = {}
    monkeypatch.setattr(mail_tools.smtplib, "SMTP_SSL", lambda *a, **kw: client)
    session = Dialogue()
    capabilities.request(
        session,
        "mail_send",
        json.dumps({"to": "to@example.com", "subject": "Тест", "body": "Проверка"}),
    )
    client.send_message.assert_not_called()
    capabilities.confirmation(session, "да")
    client.send_message.assert_called_once()
    assert session.capability is None


def test_header_injection_is_rejected():
    with pytest.raises(ValueError):
        mail_tools.message(
            {
                "to": "test@example.com",
                "subject": "Subject\nBcc: other@example.com",
                "body": "body",
            }
        )


def test_account_change_cancels_send(monkeypatch):
    monkeypatch.setattr(
        mail_tools, "profile", lambda: {"address": "changed@example.com"}
    )
    with pytest.raises(ValueError, match="изменился"):
        mail_tools.send(
            {
                "to": "to@example.com",
                "subject": "Тест",
                "body": "Текст",
                "sender": "old@example.com",
            }
        )
