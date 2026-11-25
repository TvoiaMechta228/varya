"""Прямые просьбы к сервисам Яндекса, их адреса и контекст разговора."""

from dataclasses import dataclass
import re
import time
from urllib.parse import urlencode

import actions
from command_results import Result
import config
from safety import normalize_text
import speech

CONTEXT_SECONDS = 180
MEDIA_WORDS = {
    "поставь на паузу": "pause",
    "поставь музыку на паузу": "pause",
    "пауза": "pause",
    "пауза музыки": "pause",
    "приостанови музыку": "pause",
    "продолжи музыку": "resume",
    "продолжи воспроизведение": "resume",
    "включи ее": "resume",
    "включи музыку": "resume",
    "запусти ее": "resume",
    "следующий трек": "next",
    "следующая песня": "next",
    "переключи трек": "next",
    "предыдущий трек": "previous",
    "предыдущая песня": "previous",
    "что играет": "status",
    "что сейчас играет": "status",
}
_OPEN = r"(?:открой|открыть|запусти|запустить|включи|включить|поставь|покажи|перейди)"
_WHERE = r"(?:в (?:яндексе|браузере|яндекс браузере)|через (?:браузер|яндекс браузер))"


@dataclass(frozen=True)
class Intent:
    """Проверенное намерение без произвольных адресов и системных команд."""

    service: str
    operation: str = "open"
    query: str = ""
    origin: str = ""


def service_id(value: str) -> str | None:
    """Найти сервис по целому имени, учитывая склонение названий."""
    key = normalize_text(value).replace("яндекс-", "яндекс ")
    return next(
        (name for name, item in config.YANDEX_SERVICES.items() if key in item[4]), None
    )


def parse(raw: str, session=None) -> Intent | None:
    """Разобрать просьбу целиком, не исполняя команды внутри поискового текста."""
    text = raw.strip()
    text = re.sub(r"^пожалуйста[, ]+|[, ]+пожалуйста$", "", text, flags=re.I)
    text = re.sub(r"^(?:можешь|можно попросить тебя)\s+", "", text, flags=re.I)
    text = re.sub(
        r"^((?:открой|запусти|включи|покажи)) (?:мне|нам) ", r"\1 ", text, flags=re.I
    )
    key = normalize_text(text)
    active = (
        session.yandex_service
        if session and time.monotonic() < session.yandex_until
        else ""
    )
    if key in {"что ты умеешь с яндексом", "помощь по яндексу", "команды яндекса"}:
        return Intent("all", "help")
    if active == "music" and key in MEDIA_WORDS:
        return Intent("music", MEDIA_WORDS[key])
    # Указание сервиса работает и без контекста предыдущей реплики.
    stripped = re.sub(r"(?: в| на| из)? яндекс[ .-]?музык[ауыeе]$", "", key)
    if stripped != key and stripped in MEDIA_WORDS:
        return Intent("music", MEDIA_WORDS[stripped])
    music_place = r"(?:яндекс[ .-]?музык[аеу]|яндексе)"
    if re.fullmatch(
        rf"(?:включи|запусти|поставь|продолжи)(?: (?:музыку|воспроизведение|ее|её))? (?:в|на|через) {music_place}",
        text.rstrip(".!?"),
        re.I,
    ):
        return Intent("music", "resume")
    for pattern in (
        rf"(?:включи|поставь|запусти) (?:в|на) {music_place} (.+)",
        rf"(?:включи|поставь|запусти) (.+) (?:в|на) {music_place}",
    ):
        if match := re.fullmatch(pattern, text, re.I):
            return Intent("music", "search", match[1])
    if match := re.fullmatch(r"(?:найди|поищи) там (.+)", text, re.I):
        return (
            Intent(active, "search", match[1])
            if active
            else Intent("all", "choose", match[1])
        )
    aliases = sorted(
        (
            (alias, name)
            for name, item in config.YANDEX_SERVICES.items()
            for alias in item[4]
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
    for alias, name in aliases:
        escaped = re.escape(alias)
        for pattern in (
            rf"(?:найди|поищи) (?:в|на) {escaped} (.+)",
            rf"(?:найди|поищи) (.+) (?:в|на) {escaped}",
        ):
            if match := re.fullmatch(pattern, text, re.I):
                return Intent(name, "search", match[1])
    if match := re.fullmatch(
        r"(?:найди|поищи) (?:в яндексе|через яндекс) (.+)", text, re.I
    ):
        return Intent("search", "search", match[1])
    if match := re.fullmatch(
        r"(?:построй|покажи|проложи)(?: в яндексе)? маршрут (?:от (.+?) )?до (.+?)(?: (пешком|на машине|на транспорте))?",
        text,
        re.I,
    ):
        kind = {"пешком": "pedestrian", "на транспорте": "mt"}.get(
            (match[3] or "").lower(), "auto"
        )
        return Intent("maps", "route:" + kind, match[2], match[1] or "")
    languages = {
        "английский": "en",
        "русский": "ru",
        "немецкий": "de",
        "французский": "fr",
        "испанский": "es",
    }
    if match := re.fullmatch(
        r"переведи(?: в яндексе)? на (английский|русский|немецкий|французский|испанский) (.+)",
        text,
        re.I,
    ):
        return Intent("translate", "translate:" + languages[match[1].lower()], match[2])
    if re.fullmatch(
        r"(?:включи|запусти|поставь) (?:мою волну|моя волна)(?: в яндексе)?", text, re.I
    ):
        return Intent("music", "wave")
    match = re.fullmatch(
        rf"({_OPEN})(?: {_WHERE})? (.+?)(?: {_WHERE})?", text.rstrip(".!?"), re.I
    )
    if not match:
        return None
    verb, target = match[1].lower(), match[2]
    play_after = re.fullmatch(
        r"(.+?) (?:и|а затем|и затем) (?:включи|запусти)(?: (?:ее|её|музыку|воспроизведение))?",
        target,
        re.I,
    )
    name = service_id(play_after[1] if play_after else target)
    if name is None and re.search(_WHERE, text, re.I):
        name = service_id("яндекс " + target)
    if name is None:
        # «Музыку в Яндексе» тоже имя сервиса, а обычная локальная музыка — нет.
        if re.fullmatch(r"музык[ау] (?:в|на|через) яндекс(?:е)?", target, re.I):
            name = "music"
        elif verb in {"открой", "открыть", "покажи"} and normalize_text(target) in {
            "музыку",
            "музыка",
        }:
            name = "music"
    if name:
        if play_after and name != "music":
            return None
        operation = (
            "resume"
            if name == "music"
            and (play_after or verb in {"включи", "включить", "поставь"})
            else "open"
        )
        return Intent(name, operation)
    return None


def url_for(intent: Intent) -> str:
    """Собрать адрес только из реестра и URL-кодированных данных пользователя."""
    item = config.YANDEX_SERVICES[intent.service]
    if intent.operation.startswith("route:"):
        return (
            item[1]
            + "?"
            + urlencode(
                {
                    "rtext": intent.origin + "~" + intent.query,
                    "rtt": intent.operation.split(":")[1],
                }
            )
        )
    if intent.operation.startswith("translate:"):
        return (
            item[1]
            + "?"
            + urlencode({"text": intent.query, "lang": intent.operation.split(":")[1]})
        )
    if intent.operation == "search":
        if not item[2]:
            raise ValueError(
                f"Для «{item[0]}» поиск из Вари пока не подключён. Скажи «открой {item[4][0]}»."
            )
        return item[2] + "?" + urlencode({item[3]: intent.query})
    return item[1]


def handle(session, raw: str) -> Result | None:
    """Выполнить распознанную просьбу без подмены сервиса локальным плеером."""
    intent = parse(raw, session)
    if intent is None:
        return None
    if intent.operation == "help":
        return Result(
            "Яндекс: открываю сервисы, ищу в Музыке, Картах, Маркете, Картинках, Видео и Кинопоиске; строю маршрут и открываю перевод. Музыка: включить, пауза, продолжить, следующий трек. Примеры: «открой в Яндексе Яндекс Музыку и включи её», «найди в Яндекс Музыке Кино», «найди на Яндекс Картах аптеку». Действия с письмами, файлами и покупками не выполняются открытием сайта."
        )
    if intent.operation == "choose":
        return Result(
            "В каком сервисе искать? Например, «найди в Яндекс Музыке Кино».", ok=False
        )
    import missions

    if missions.mode() == "Наблюдатель" and intent.operation != "status":
        return Result(
            "В режиме наблюдателя управление сервисами отключено. Выбери «Помощник» в настройках.",
            ok=False,
        )
    if speech.is_cancelled():
        raise InterruptedError
    if intent.service == "music" and intent.operation in {
        "resume",
        "wave",
        "pause",
        "next",
        "previous",
        "status",
    }:
        import yandex_media

        active = (
            session.yandex_service == "music"
            and time.monotonic() < session.yandex_until
        )
        if intent.operation in {"resume", "wave"} and not active:
            actions._open_url(config.YANDEX_SERVICES["music"][1])
        session.yandex_service, session.yandex_until = (
            "music",
            time.monotonic() + CONTEXT_SECONDS,
        )
        return yandex_media.control(intent.operation)
    url = url_for(intent)
    if intent.service == "search" and intent.operation == "search":
        actions.search_web(intent.query)
    else:
        actions._open_url(url)
    session.yandex_service, session.yandex_until = (
        intent.service,
        time.monotonic() + CONTEXT_SECONDS,
    )
    title = config.YANDEX_SERVICES[intent.service][0]
    if intent.operation == "search":
        return Result(
            f"Открываю поиск «{intent.query}» в сервисе «{title}»."
            + (
                " Выбери нужный трек на странице; затем доступны пауза, продолжение и переключение."
                if intent.service == "music"
                else ""
            )
        )
    if intent.operation.startswith("route:"):
        return Result(
            f"Открываю маршрут в Яндекс Картах: {intent.origin or 'текущее местоположение'} → {intent.query}. Проверь точки маршрута на карте."
        )
    if intent.operation.startswith("translate:"):
        return Result(
            "Открываю текст в Яндекс Переводчике. Перевод появится на странице."
        )
    return Result(f"Открываю {title}.")
