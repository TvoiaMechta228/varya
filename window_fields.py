"""Поля разных Windows-приложений через ValuePattern и TextPattern UI Automation."""

import hashlib

import comtypes.client

import desktop
import speech
import window_buttons


def fields(target: tuple) -> list[dict]:
    if tuple(target) not in desktop.windows():
        raise ValueError("Окно изменилось или закрыто. Выбери его заново.")
    ui = comtypes.client.GetModule("UIAutomationCore.dll")
    client = comtypes.client.CreateObject(ui.CUIAutomation, interface=ui.IUIAutomation)
    root = client.ElementFromHandle(target[0])
    condition = client.CreateOrCondition(
        client.CreatePropertyCondition(
            ui.UIA_ControlTypePropertyId, ui.UIA_EditControlTypeId
        ),
        client.CreatePropertyCondition(
            ui.UIA_ControlTypePropertyId, ui.UIA_DocumentControlTypeId
        ),
    )
    elements = root.FindAll(ui.TreeScope_Descendants, condition)
    result = []
    for index in range(min(elements.Length, 300)):
        item = elements.GetElement(index)
        if (
            item.CurrentIsOffscreen
            or not item.CurrentIsEnabled
            or item.CurrentIsPassword
        ):
            continue
        name = item.CurrentName or item.CurrentAutomationId or f"Поле {len(result) + 1}"
        value, writable = "", False
        if item.GetCurrentPropertyValue(ui.UIA_IsValuePatternAvailablePropertyId):
            pattern = item.GetCurrentPattern(ui.UIA_ValuePatternId).QueryInterface(
                ui.IUIAutomationValuePattern
            )
            value = pattern.CurrentValue
            writable = not pattern.CurrentIsReadOnly
        elif item.GetCurrentPropertyValue(ui.UIA_IsTextPatternAvailablePropertyId):
            pattern = item.GetCurrentPattern(ui.UIA_TextPatternId).QueryInterface(
                ui.IUIAutomationTextPattern
            )
            value = pattern.DocumentRange.GetText(4000)
        result.append(
            {
                "name": name,
                "value": value,
                "writable": writable,
                "runtime": tuple(item.GetRuntimeId()),
                "element": item,
                "ui": ui,
            }
        )
        if len(result) >= 40:
            break
    return result


def describe(session, name: str = "") -> str:
    targets = window_buttons.select_window(name, session.selected_window)
    if len(targets) != 1:
        raise ValueError("Назови одно окно из списка открытых окон")
    session.selected_window = targets[0]
    items = fields(targets[0])
    session.field_choices = tuple(
        {key: value for key, value in item.items() if key not in {"element", "ui"}}
        for item in items
    )
    return (
        "\n".join(
            f"{i}. {item['name']} — "
            + ("доступна запись" if item["writable"] else "только чтение")
            + "\n"
            + item["value"][:400]
            for i, item in enumerate(items, 1)
        )
        or "Windows не предоставляет текстовых полей этого окна."
    )


def prepare(session, data: dict) -> dict:
    if data.get("window"):
        describe(session, data["window"])
    if session.selected_window is None:
        raise ValueError("Сначала скажи «покажи поля окна …»")
    selection = str(data["field"])
    matches = [
        item
        for index, item in enumerate(session.field_choices, 1)
        if selection == str(index) or selection.casefold() == item["name"].casefold()
    ]
    if len(matches) != 1:
        raise ValueError("Укажи номер или точную подпись поля")
    item = matches[0]
    if not item["writable"]:
        raise ValueError(
            "Это поле Windows предоставляет только для чтения. Для документа можно использовать команды работы с файлом."
        )
    if not isinstance(data.get("text"), str) or len(data["text"]) > 2000:
        raise ValueError("Укажи текст до 2000 символов")
    return {
        "name": item["name"],
        "text": data["text"],
        "_window": tuple(session.selected_window),
        "_runtime": item["runtime"],
        "_previous": hashlib.sha256(item["value"].encode()).hexdigest(),
    }


def fill(data: dict) -> str:
    for item in fields(tuple(data["_window"])):
        if item["runtime"] != tuple(data["_runtime"]):
            continue
        if hashlib.sha256(item["value"].encode()).hexdigest() != data["_previous"]:
            raise ValueError("Содержимое поля изменилось. Запроси ввод заново.")
        if speech.is_cancelled():
            raise InterruptedError
        pattern = (
            item["element"]
            .GetCurrentPattern(item["ui"].UIA_ValuePatternId)
            .QueryInterface(item["ui"].IUIAutomationValuePattern)
        )
        pattern.SetValue(data["text"])
        if pattern.CurrentValue != data["text"]:
            raise OSError("Windows не подтвердила значение поля")
        return "Поле «" + data["name"] + "» заполнено. Значение проверено."
    raise ValueError("Выбранное поле больше недоступно")
