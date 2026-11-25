"""Регрессии реальных просьб пользователя и границ сервисов Яндекса."""

import time
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

import actions
import capabilities
from command_results import Result
import config
from dialogue import Dialogue
import main
import music_tools
import settings
import speech
import yandex_media
import yandex_tools as yandex


@pytest.mark.parametrize(
    "text,operation",
    [
        ("открой в яндексе яндекс музыку", "open"),
        ("открой в браузере яндекс музыку", "open"),
        ("открой в яндексе яндекс музыку и включи ее", "resume"),
        ("открой Яндекс Музыку и включи её", "resume"),
        ("включи Яндекс Музыку", "resume"),
        ("включи музыку в Яндекс Музыке", "resume"),
        ("продолжи музыку в яндексе", "resume"),
        ("запусти музыку в яндексе", "resume"),
        ("Можешь открыть Яндекс Музыку?", "open"),
        ("открой мне яндекс музыку пожалуйста", "open"),
        ("открой Яндекс.Музыку", "open"),
        ("включи мою волну", "wave"),
        ("поставь музыку на паузу в яндекс музыке", "pause"),
        ("следующий трек в яндекс музыке", "next"),
    ],
)
def test_screenshot_phrases_are_yandex_not_local(text, operation):
    intent = yandex.parse(text)
    assert intent and intent.service == "music" and intent.operation == operation


@pytest.mark.parametrize(
    "text,service,query",
    [
        ("найди в Яндекс Музыке Кино", "music", "Кино"),
        ("найди Кино в Яндекс Музыке", "music", "Кино"),
        ("поищи в музыке Яндекса Кино", "music", "Кино"),
        ("найди на Яндекс Картах аптеку", "maps", "аптеку"),
        ("найди на Яндекс Маркете SSD 2 ТБ", "market", "SSD 2 ТБ"),
        ("найди в Яндекс Картинках C++ & Python", "images", "C++ & Python"),
        ("найди в Яндексе рецепт сырников", "search", "рецепт сырников"),
        ("найди в Кинопоиске Интерстеллар", "kinopoisk", "Интерстеллар"),
        ("найди в Яндекс Видео уроки Python", "video", "уроки Python"),
    ],
)
def test_specific_service_wins_over_generic_yandex(text, service, query):
    intent = yandex.parse(text)
    assert (intent.service, intent.query) == (service, query)
    assert any(
        values == [query]
        for values in parse_qs(urlsplit(yandex.url_for(intent)).query).values()
    )


@pytest.mark.parametrize("service", list(config.YANDEX_SERVICES))
def test_all_catalog_entries_open_a_fixed_https_host(service):
    item = config.YANDEX_SERVICES[service]
    intent = yandex.parse("открой " + item[4][0])
    assert intent and intent.service == service
    parts = urlsplit(yandex.url_for(intent))
    assert parts.scheme == "https"
    assert parts.hostname and not parts.username


@pytest.mark.parametrize(
    "text",
    [
        "не открывай яндекс музыку",
        "как открыть яндекс музыку",
        "запомни открой яндекс музыку",
        "открой яндекс музыку и удали все файлы",
        "включи музыку",
        "открой папку музыка",
        "открой ya.ru.other.invalid",
    ],
)
def test_unrelated_and_unsafe_text_is_not_a_yandex_action(text):
    assert yandex.parse(text) is None


def test_real_routing_preserves_music_context(monkeypatch):
    session = Dialogue()
    monkeypatch.setattr(actions, "_open_url", launch := Mock())
    monkeypatch.setattr(
        yandex_media,
        "control",
        media := Mock(return_value=Result("Воспроизведение включено.")),
    )
    monkeypatch.setattr(music_tools, "play", local := Mock())
    monkeypatch.setattr(settings, "_current", {**settings.DEFAULTS, "wake_word": False})
    for text in (
        "открой в яндексе яндекс музыку",
        "включи ее",
        "поставь музыку на паузу",
        "следующий трек",
    ):
        assert main.route_command(text, dialogue=session, typed=True)
    launch.assert_called_once_with("https://music.yandex.ru/")
    assert [call.args[0] for call in media.call_args_list] == [
        "resume",
        "pause",
        "next",
    ]
    local.assert_not_called()


def test_compound_from_screenshot_does_not_enter_sequence_plan(monkeypatch):
    session = Dialogue()
    monkeypatch.setattr(actions, "_open_url", launch := Mock())
    monkeypatch.setattr(
        yandex_media, "control", media := Mock(return_value=Result("Включено."))
    )
    assert main.route_command(
        "открой в яндексе яндекс музыку и включи ее", dialogue=session, typed=True
    )
    assert session.mission is None
    launch.assert_called_once_with("https://music.yandex.ru/")
    media.assert_called_once_with("resume")
    assert "план" not in speech.speak.call_args.args[0]


def test_context_search_and_expiration(monkeypatch):
    session = Dialogue(yandex_service="music", yandex_until=time.monotonic() + 60)
    intent = yandex.parse("найди там Кино и открой блокнот", session)
    assert intent.service == "music" and intent.query == "Кино и открой блокнот"
    session.yandex_until = 0
    assert yandex.parse("найди там Кино", session).operation == "choose"
    assert yandex.parse("включи ее", session) is None


def test_query_is_data_not_a_second_action(monkeypatch):
    monkeypatch.setattr(actions, "_open_url", launch := Mock())
    query = "Кино & text=другое # и открой блокнот"
    result = yandex.handle(Dialogue(), "найди в Яндекс Музыке " + query)
    url = launch.call_args.args[0]
    assert parse_qs(urlsplit(url).query) == {"text": [query]}
    assert "Кино" in result.text


def test_observer_cannot_open_or_play(monkeypatch):
    monkeypatch.setattr(
        settings, "_current", {**settings.DEFAULTS, "control_mode": "Наблюдатель"}
    )
    # Используем тот же источник режима, что и основной маршрутизатор.
    import missions

    monkeypatch.setattr(missions, "mode", lambda: "Наблюдатель")
    monkeypatch.setattr(actions, "_open_url", launch := Mock())
    monkeypatch.setattr(yandex_media, "control", media := Mock())
    assert not capabilities.direct(Dialogue(), "включи яндекс музыку").ok
    launch.assert_not_called()
    media.assert_not_called()


def test_local_music_explicitly_leaves_yandex_context(monkeypatch):
    session = Dialogue(yandex_service="music", yandex_until=time.monotonic() + 60)
    monkeypatch.setattr(
        music_tools, "play", play := Mock(return_value="Играет локальный трек")
    )
    capabilities.direct(session, "включи локальную музыку")
    play.assert_called_once_with("")
    assert session.yandex_service == ""


def test_map_route_encodes_both_addresses():
    intent = yandex.parse(
        "построй маршрут от Екатеринбург, Мира 1 до Пермь, Ленина 2 пешком"
    )
    assert intent.service == "maps"
    assert parse_qs(urlsplit(yandex.url_for(intent)).query) == {
        "rtext": ["Екатеринбург, Мира 1~Пермь, Ленина 2"],
        "rtt": ["pedestrian"],
    }


def test_translation_preserves_text_and_language():
    intent = yandex.parse("переведи на английский Привет, мир & Python!")
    assert parse_qs(urlsplit(yandex.url_for(intent)).query) == {
        "text": ["Привет, мир & Python!"],
        "lang": ["en"],
    }


def test_unsupported_mail_search_does_not_read_or_send_account_data(monkeypatch):
    monkeypatch.setattr(actions, "_open_url", launch := Mock())
    with pytest.raises(ValueError, match="пока не подключён"):
        yandex.handle(Dialogue(), "найди в Яндекс Почте договор")
    launch.assert_not_called()


@pytest.mark.parametrize(
    "text", ["включи Земфиру в Яндекс Музыке", "поставь в Яндекс Музыке Кино"]
)
def test_artist_request_never_falls_back_to_local_files(text):
    intent = yandex.parse(text)
    assert intent.service == "music" and intent.operation == "search"


def test_sequence_with_yandex_stops_after_unconfirmed_play(monkeypatch):
    monkeypatch.setattr(actions, "_open_url", opened := Mock())
    monkeypatch.setattr(actions, "open_app", app := Mock(return_value=True))
    monkeypatch.setattr(
        yandex_media,
        "control",
        media := Mock(return_value=Result("Нужен вход", ok=False)),
    )
    session = Dialogue()
    main.route_command(
        "открой Яндекс Музыку, затем включи ее, затем открой блокнот",
        dialogue=session,
        typed=True,
    )
    opened.assert_called_once()
    media.assert_called_once_with("resume")
    app.assert_not_called()
    assert "1 из 3" in session.last_result
