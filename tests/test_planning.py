"""Планы не пропускают подтверждения и не повторяют неясно завершившиеся шаги."""

import json

from dialogue import Dialogue
import main
import planning


def plan_for(path):
    value = {
        "id": "abc123def456",
        "request": "Создать и прочитать файл",
        "position": 0,
        "status": "paused",
        "results": [],
        "steps": [
            {
                "action": "write_text",
                "argument": json.dumps({"path": str(path), "text": "Проверка"}),
            },
            {"action": "file_read", "argument": str(path)},
        ],
    }
    planning.save(value)
    return value


def test_plan_start_does_not_skip_write_confirmation(tmp_path):
    path = tmp_path / "result.txt"
    plan = plan_for(path)
    session = Dialogue()
    main.route_command("продолжи план " + plan["id"], dialogue=session, typed=True)
    assert session.capability.action == "plan_resume"
    main.route_command("да", dialogue=session, typed=True)
    assert not path.exists()
    assert session.capability.action == "write_text"
    assert planning.load(plan["id"])["position"] == 0
    main.route_command("да", dialogue=session, typed=True)
    assert path.read_text(encoding="utf-8") == "Проверка"
    assert planning.load(plan["id"])["status"] == "done"
    assert session.plan_id == ""


def test_plan_denial_pauses_without_changes(tmp_path):
    path = tmp_path / "result.txt"
    plan = plan_for(path)
    session = Dialogue()
    planning.start(session, plan)
    main.route_command("нет", dialogue=session, typed=True)
    assert not path.exists()
    assert planning.load(plan["id"])["status"] == "paused"


def test_intervening_request_detaches_plan(tmp_path):
    path = tmp_path / "result.txt"
    plan = plan_for(path)
    session = Dialogue()
    planning.start(session, plan)
    main.route_command("который час", dialogue=session, typed=True)
    assert not session.plan_id and not path.exists()
    assert planning.load(plan["id"])["position"] == 0


def test_uncertain_step_requires_explicit_resolution(tmp_path):
    import pytest

    plan = plan_for(tmp_path / "result.txt")
    plan["status"] = "running"
    planning.save(plan)
    with pytest.raises(ValueError, match="прервался"):
        planning.prepare(plan["id"], resume=True)
    resolved = planning.prepare(plan["id"] + " последний шаг выполнен", resume=True)
    assert resolved["_recovery"] is True
    assert planning.load(plan["id"])["position"] == 0


def test_failed_music_does_not_report_plan_completed(tmp_path, monkeypatch):
    import music_tools

    plan = plan_for(tmp_path / "result.txt")
    plan["steps"].insert(0, {"action": "music_play", "argument": ""})
    planning.save(plan)

    def unavailable(_):
        raise ValueError("Нет аудиофайлов")

    monkeypatch.setattr(music_tools, "play", unavailable)
    result = planning.start(Dialogue(), plan)
    assert not result.ok
    assert planning.load(plan["id"])["status"] == "paused"
    assert planning.load(plan["id"])["position"] == 0
    assert not (tmp_path / "result.txt").exists()
