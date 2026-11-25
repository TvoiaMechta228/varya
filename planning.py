"""Сохраняемые планы из зарегистрированных инструментов с подтверждением каждого изменения."""

import asyncio
import json
import uuid

from command_results import Result
import speech
from workspace_tasks import database


def table(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS tool_plans(id TEXT PRIMARY KEY, request TEXT, steps TEXT, position INTEGER, status TEXT, results TEXT)"
    )


def load(identifier: str) -> dict:
    with database() as db:
        table(db)
        row = db.execute(
            "SELECT id,request,steps,position,status,results FROM tool_plans WHERE id=?",
            (identifier,),
        ).fetchone()
    if row is None:
        raise ValueError("План с таким идентификатором не найден")
    return {
        "id": row[0],
        "request": row[1],
        "steps": json.loads(row[2]),
        "position": row[3],
        "status": row[4],
        "results": json.loads(row[5]),
    }


def save(plan: dict) -> None:
    with database() as db:
        table(db)
        db.execute(
            "INSERT OR REPLACE INTO tool_plans VALUES(?,?,?,?,?,?)",
            (
                plan["id"],
                plan["request"],
                json.dumps(plan["steps"], ensure_ascii=False),
                plan["position"],
                plan["status"],
                json.dumps(plan["results"], ensure_ascii=False),
            ),
        )


def pending() -> list[dict]:
    with database() as db:
        table(db)
        return [
            {"id": row[0], "request": row[1], "position": row[2], "status": row[3]}
            for row in db.execute(
                "SELECT id,request,position,status FROM tool_plans WHERE status!='done' ORDER BY rowid DESC LIMIT 20"
            )
        ]


def prepare(description: str, *, resume: bool = False) -> dict:
    import capabilities

    if resume:
        import re

        recovery = re.fullmatch(
            r"([a-f0-9]{12}) последний шаг (выполнен|не выполнен)",
            description.strip(),
            re.I,
        )
        identifier = recovery[1] if recovery else description.strip()
        plan = load(identifier)
        if plan["status"] == "done":
            raise ValueError("Этот план уже выполнен")
        if plan["status"] == "running":
            if not recovery:
                raise ValueError(
                    "План прервался во время шага. Проверь последний результат. На вкладке задач можно подтвердить выполнение или запросить повтор шага."
                )
            plan["_recovery"] = recovery[2].casefold() == "выполнен"
        return plan
    if not 10 <= len(description.strip()) <= 1800:
        raise ValueError(
            "Опиши поручение с нужными файлами, адресами и ожидаемым результатом"
        )
    allowed = {
        key: text
        for key, text in capabilities.DESCRIPTIONS.items()
        if not key.startswith("plan_")
    }
    allowed.update(
        {
            "app": "открыть программу; argument — имя",
            "folder": "открыть папку Windows; argument — имя",
            "timer": "таймер; argument — JSON {name,seconds}",
            "note": "заметка; argument — текст",
        }
    )
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "action": {"type": "string", "enum": list(allowed)},
                        "argument": {"type": "string"},
                    },
                    "required": ["action", "argument"],
                },
            }
        },
        "required": ["steps"],
    }
    import local_brain

    speech.publish("state", "Составляю план действий…")
    result = asyncio.run(
        local_brain.complete(
            [
                {
                    "role": "system",
                    "content": "Разложи поручение на действия из списка. Верни JSON steps[{action,argument}]. "
                    "Не выдумывай пути, адреса и получателей. Аргументы JSON-параметров передавай строкой с JSON. "
                    "Если не хватает данных, верни пустой argument в соответствующем шаге, пользователь уточнит. "
                    "Никаких системных команд или программного кода. Список инструментов\n"
                    + json.dumps(allowed, ensure_ascii=False),
                },
                {"role": "user", "content": description},
            ],
            schema,
            max_tokens=1400,
        )
    )
    steps = json.loads(result).get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
        raise ValueError("Не удалось составить план до 12 шагов. Уточни поручение.")
    for item in steps:
        if (
            not isinstance(item, dict)
            or item.get("action") not in allowed
            or not isinstance(item.get("argument"), str)
        ):
            raise ValueError("Модель предложила неизвестное действие")
        if len(item["argument"]) > 2000:
            raise ValueError("Слишком длинный аргумент шага")
    plan = {
        "id": uuid.uuid4().hex[:12],
        "request": description,
        "steps": steps,
        "position": 0,
        "status": "offered",
        "results": [],
    }
    save(plan)
    return plan


def start(session, plan: dict) -> Result:
    if "_recovery" in plan:
        if plan.pop("_recovery"):
            plan["position"] += 1
            plan["results"].append(
                "Пользователь подтвердил завершение прерванного шага"
            )
        else:
            plan["results"].append(
                "Пользователь проверил результат и запросил повтор прерванного шага"
            )
        plan["status"] = "paused"
        save(plan)
    session.plan_id = plan["id"]
    session.plan_waiting = False
    return advance(session)


def mark_running(session) -> None:
    plan = load(session.plan_id)
    plan["status"] = "running"
    save(plan)


def pause(session, reason: str) -> None:
    if not session.plan_id:
        return
    plan = load(session.plan_id)
    plan["status"] = "paused"
    plan["results"].append(reason)
    save(plan)
    session.plan_id = ""
    session.plan_waiting = False


def advance(session, completed: Result | None = None) -> Result:
    import capabilities
    import config
    import actions
    import requests_pc
    from understanding import Intent

    plan = load(session.plan_id)
    if completed is not None and session.plan_waiting:
        session.plan_waiting = False
        if completed.card:
            speech.publish(completed.card, completed.value)
        plan["results"].append(completed.text)
        if not completed.ok:
            plan["status"] = "paused"
            save(plan)
            session.plan_id = ""
            return Result(
                completed.text + " План приостановлен. Идентификатор — " + plan["id"],
                ok=False,
            )
        plan["position"] += 1
        save(plan)
    while plan["position"] < len(plan["steps"]):
        if speech.is_cancelled():
            plan["status"] = "paused"
            save(plan)
            return Result(
                "План остановлен. Продолжить можно по идентификатору " + plan["id"],
                ok=False,
            )
        step = plan["steps"][plan["position"]]
        speech.publish(
            "mission",
            f"Шаг {plan['position'] + 1}/{len(plan['steps'])} · {step['action']}",
        )
        plan["status"] = "running"
        save(plan)
        try:
            if step["action"] == "app":
                name = step["argument"]
                if name not in config.APP_WHITELIST or not actions.open_app(name):
                    raise ValueError("Программа шага не найдена в разрешённом списке")
                result = Result("Запрошен запуск «" + name + "».")
            elif step["action"] == "folder":
                if step["argument"] not in requests_pc.FOLDERS:
                    raise ValueError("Уточни имя папки в шаге")
                result = requests_pc.open_location("folder", step["argument"])
            elif step["action"] == "timer":
                data = json.loads(step["argument"])
                result = requests_pc.perform(
                    session, Intent("timer", data["name"], int(data["seconds"]))
                )
            elif step["action"] == "note":
                result = requests_pc.perform(session, Intent("note", step["argument"]))
            else:
                result = capabilities.request(session, step["action"], step["argument"])
            if session.capability is not None:
                session.plan_waiting = True
                plan["status"] = "waiting"
                save(plan)
                return result
            plan["results"].append(result.text)
            if result.card:
                speech.publish(result.card, result.value)
            if not result.ok:
                raise ValueError(result.text)
            plan["position"] += 1
            save(plan)
        except Exception as error:
            plan["status"] = "paused"
            plan["results"].append(str(error))
            save(plan)
            session.plan_id = ""
            return Result(
                f"План остановлен на шаге {plan['position'] + 1}. {error}\nДля продолжения — «продолжи план {plan['id']}».",
                ok=False,
            )
    plan["status"] = "done"
    save(plan)
    session.plan_id = ""
    return Result("План выполнен. " + "\n".join(plan["results"]))
