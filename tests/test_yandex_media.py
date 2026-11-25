"""Плеер не нажимает неоднозначные кнопки и не выдаёт запрос за успех."""

import pytest

import yandex_media as media


class Page:
    def __init__(self, before, after=None):
        self.current, self.after, self.clicked = before, after, []

    def snapshot(self):
        return self.current

    def invoke(self, name):
        self.clicked.append(name)
        if self.after:
            self.current = self.after


def test_resume_verifies_player_state():
    page = Page(media.Snapshot(("Воспроизведение",)), media.Snapshot(("Пауза",)))
    assert media.operate(page, "resume")["ok"]
    assert page.clicked == ["Воспроизведение"]


def test_wave_uses_only_wave_button():
    page = Page(
        media.Snapshot(("Воспроизведение моей волны", "Оформить Плюс")),
        media.Snapshot(("Пауза моей волны",)),
    )
    assert media.operate(page, "wave")["ok"]
    assert page.clicked == ["Воспроизведение моей волны"]


def test_pause_verifies_player_state():
    page = Page(media.Snapshot(("Пауза",)), media.Snapshot(("Воспроизведение",)))
    assert media.operate(page, "pause")["ok"]


def test_already_playing_is_not_toggled_off():
    page = Page(media.Snapshot(("Пауза",)))
    assert media.operate(page, "resume")["ok"]
    assert page.clicked == []


@pytest.mark.parametrize(
    "names", [(), ("Воспроизведение", "Воспроизведение"), ("Оформить Плюс",)]
)
def test_unknown_or_ambiguous_buttons_are_not_clicked(names):
    page = Page(media.Snapshot(names))
    assert not media.operate(page, "resume")["ok"]
    assert not page.clicked


@pytest.mark.parametrize(
    "text",
    [
        "Яндекс Музыка недоступна в вашем регионе",
        "Вы не робот?",
        "Войдите, чтобы слушать",
        "Оформите подписку",
    ],
)
def test_site_restrictions_do_not_trigger_play(text):
    page = Page(media.Snapshot(("Воспроизведение",), text))
    assert not media.operate(page, "resume")["ok"]
    assert not page.clicked


def test_unconfirmed_play_is_not_success(monkeypatch):
    clock = iter((0, 4))
    monkeypatch.setattr(media, "monotonic", lambda: next(clock))
    page = Page(media.Snapshot(("Воспроизведение",)))
    assert not media.operate(page, "resume")["ok"]
    assert page.clicked == ["Воспроизведение"]


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("https://music.yandex.ru/search", True),
        ("music.yandex.ru", True),
        ("https://music.yandex.ru.other.invalid", False),
        ("https://music.yandex.ru@other.invalid", False),
        ("https://user:pass@music.yandex.ru", False),
        ("file://music.yandex.ru", False),
    ],
)
def test_music_document_address_is_verified(url, allowed):
    assert media.music_url(url) is allowed


def test_unknown_media_action_rejected_before_starting_process(monkeypatch):
    from unittest.mock import Mock

    monkeypatch.setattr(media.subprocess, "Popen", popen := Mock())
    with pytest.raises(ValueError):
        media.control("buy subscription")
    popen.assert_not_called()


def test_timeout_releases_helper_process(monkeypatch):
    from unittest.mock import Mock

    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(media.subprocess, "Popen", Mock(return_value=process))
    clock = iter((0, 13))
    monkeypatch.setattr(media, "monotonic", lambda: next(clock))
    result = media.control("resume")
    assert not result.ok
    process.kill.assert_called_once()
    process.wait.assert_called_once_with(timeout=3)


def test_cancellation_releases_helper_process(monkeypatch):
    from unittest.mock import Mock
    import speech

    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(media.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(speech, "is_cancelled", Mock(side_effect=[False, True]))
    with pytest.raises(InterruptedError):
        media.control("resume")
    process.kill.assert_called_once()
