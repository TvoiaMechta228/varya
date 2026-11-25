"""Проект не может выйти за папку или опубликовать синтаксически неверный Python."""

import pytest
import project_generation


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "C:/outside.py",
        "CON.txt",
        "file.txt.",
        "name?.py",
        "a/../file.py",
    ],
)
def test_invalid_project_path(path):
    with pytest.raises(ValueError):
        project_generation.validate({"files": [{"path": path, "content": ""}]})


def test_python_is_parsed_before_publication():
    with pytest.raises(ValueError, match="ошибочный Python"):
        project_generation.validate(
            {"files": [{"path": "main.py", "content": "def :"}]}
        )


def test_case_insensitive_duplicate_is_rejected():
    with pytest.raises(ValueError, match="Повторяющееся"):
        project_generation.validate(
            {
                "files": [
                    {"path": "Main.py", "content": ""},
                    {"path": "main.py", "content": ""},
                ]
            }
        )


def test_valid_project():
    result = project_generation.validate(
        {"files": [{"path": "src/main.py", "content": "print('Привет')"}]}
    )
    assert result[0]["path"] == "src/main.py"
