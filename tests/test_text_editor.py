from pathlib import Path

import pytest

from wiley_harness_agent.agent.text_editor import TextEditorError, execute_text_editor


def test_create_and_view_range(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "example.txt"

    execute_text_editor(
        {"mode": "create", "path": str(path), "file_text": "one\ntwo\nthree\n"}
    )

    assert execute_text_editor(
        {"mode": "view", "path": str(path), "view_range": [2, 3]}
    ) == "two\nthree\n"


def test_create_refuses_to_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "example.txt"
    path.write_text("existing", encoding="utf-8")

    with pytest.raises(TextEditorError, match="already exists"):
        execute_text_editor(
            {"mode": "create", "path": str(path), "file_text": "replacement"}
        )


def test_str_replace_requires_a_unique_match(tmp_path: Path) -> None:
    path = tmp_path / "example.txt"
    path.write_text("same same", encoding="utf-8")

    with pytest.raises(TextEditorError, match="exactly once"):
        execute_text_editor(
            {
                "mode": "str_replace",
                "path": str(path),
                "old_str": "same",
                "new_str": "new",
            }
        )


def test_str_replace_updates_file(tmp_path: Path) -> None:
    path = tmp_path / "example.txt"
    path.write_text("before\nafter\n", encoding="utf-8")

    execute_text_editor(
        {
            "mode": "str_replace",
            "path": str(path),
            "old_str": "before",
            "new_str": "updated",
        }
    )

    assert path.read_text(encoding="utf-8") == "updated\nafter\n"


def test_insert_uses_one_based_line_numbers(tmp_path: Path) -> None:
    path = tmp_path / "example.txt"
    path.write_text("one\nthree\n", encoding="utf-8")

    execute_text_editor(
        {
            "mode": "insert",
            "path": str(path),
            "insert_line": 2,
            "insert_text": "two",
        }
    )

    assert path.read_text(encoding="utf-8") == "one\ntwo\nthree\n"

