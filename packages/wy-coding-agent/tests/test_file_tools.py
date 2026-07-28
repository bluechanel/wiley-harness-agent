from pathlib import Path

import pytest

from wy_coding_agent.tools.edit import EDIT
from wy_coding_agent.tools.files import FileToolError
from wy_coding_agent.tools.read import READ
from wy_coding_agent.tools.write import WRITE


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- read ---


def test_read_returns_cat_n_format(workspace: Path) -> None:
    (workspace / "a.txt").write_text("hello\nworld\n", encoding="utf-8")

    assert READ.execute({"file_path": "a.txt"}) == "     1\thello\n     2\tworld"


def test_read_offset_and_limit_window(workspace: Path) -> None:
    (workspace / "a.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    result = READ.execute({"file_path": "a.txt", "offset": 2, "limit": 2})

    assert "     2\ttwo\n     3\tthree" in result
    assert "(File has more lines. Showing lines 2-3 of 4." in result


def test_read_empty_file_warns(workspace: Path) -> None:
    (workspace / "a.txt").write_text("", encoding="utf-8")

    assert "contents are empty" in READ.execute({"file_path": "a.txt"})


def test_read_offset_past_eof_warns(workspace: Path) -> None:
    (workspace / "a.txt").write_text("one\n", encoding="utf-8")

    result = READ.execute({"file_path": "a.txt", "offset": 5})

    assert "shorter than the provided offset (5)" in result
    assert "has 1 lines" in result


def test_read_missing_file_suggests_similar(workspace: Path) -> None:
    (workspace / "app.js").write_text("x\n", encoding="utf-8")

    with pytest.raises(FileToolError, match=r"Did you mean .*app\.js"):
        READ.execute({"file_path": "app.ts"})


def test_read_rejects_directory_binary_and_devices(workspace: Path) -> None:
    (workspace / "sub").mkdir()
    (workspace / "img.png").write_bytes(b"\x89PNG")

    with pytest.raises(FileToolError, match="directory"):
        READ.execute({"file_path": "sub"})
    with pytest.raises(FileToolError, match="binary"):
        READ.execute({"file_path": "img.png"})
    with pytest.raises(FileToolError, match="device file"):
        READ.execute({"file_path": "/dev/zero"})


def test_read_truncates_long_lines(workspace: Path) -> None:
    (workspace / "a.txt").write_text("x" * 3000 + "\n", encoding="utf-8")

    result = READ.execute({"file_path": "a.txt"})

    assert "(line truncated)" in result
    assert len(result) < 2200


# --- edit ---


def test_edit_replaces_single_occurrence(workspace: Path) -> None:
    path = workspace / "a.txt"
    path.write_text("hello world\n", encoding="utf-8")

    result = EDIT.execute(
        {"file_path": "a.txt", "old_string": "hello", "new_string": "goodbye"}
    )

    assert "has been updated successfully" in result
    assert path.read_text(encoding="utf-8") == "goodbye world\n"


def test_edit_rejects_identical_strings(workspace: Path) -> None:
    with pytest.raises(FileToolError, match="exactly the same"):
        EDIT.execute({"file_path": "a.txt", "old_string": "x", "new_string": "x"})


def test_edit_missing_file_raises(workspace: Path) -> None:
    with pytest.raises(FileToolError, match="File does not exist"):
        EDIT.execute({"file_path": "gone.txt", "old_string": "a", "new_string": "b"})


def test_edit_rejects_ambiguous_match_and_replace_all_resolves(workspace: Path) -> None:
    path = workspace / "a.txt"
    path.write_text("same same\n", encoding="utf-8")

    with pytest.raises(FileToolError, match="Found 2 matches"):
        EDIT.execute({"file_path": "a.txt", "old_string": "same", "new_string": "new"})

    result = EDIT.execute(
        {"file_path": "a.txt", "old_string": "same", "new_string": "new", "replace_all": True}
    )
    assert "All 2 occurrences" in result
    assert path.read_text(encoding="utf-8") == "new new\n"


def test_edit_string_not_found(workspace: Path) -> None:
    (workspace / "a.txt").write_text("hello\n", encoding="utf-8")

    with pytest.raises(FileToolError, match="not found in file"):
        EDIT.execute({"file_path": "a.txt", "old_string": "zzz", "new_string": "x"})


def test_edit_creates_file_with_empty_old_string(workspace: Path) -> None:
    result = EDIT.execute(
        {"file_path": "sub/new.txt", "old_string": "", "new_string": "content\n"}
    )

    assert "File created successfully" in result
    assert (workspace / "sub" / "new.txt").read_text(encoding="utf-8") == "content\n"


def test_edit_empty_old_string_rejects_existing_content(workspace: Path) -> None:
    (workspace / "a.txt").write_text("data\n", encoding="utf-8")

    with pytest.raises(FileToolError, match="file already exists"):
        EDIT.execute({"file_path": "a.txt", "old_string": "", "new_string": "x"})


def test_edit_preserves_crlf_line_endings(workspace: Path) -> None:
    path = workspace / "a.txt"
    path.write_bytes(b"hello\r\nworld\r\n")

    EDIT.execute({"file_path": "a.txt", "old_string": "hello", "new_string": "hi"})

    assert path.read_bytes() == b"hi\r\nworld\r\n"


def test_sequential_edits_apply(workspace: Path) -> None:
    path = workspace / "a.txt"
    path.write_text("one two\n", encoding="utf-8")

    EDIT.execute({"file_path": "a.txt", "old_string": "one", "new_string": "1"})
    EDIT.execute({"file_path": "a.txt", "old_string": "two", "new_string": "2"})

    assert path.read_text(encoding="utf-8") == "1 2\n"


# --- write ---


def test_write_creates_new_file_with_parents(workspace: Path) -> None:
    result = WRITE.execute({"file_path": "nested/dir/a.txt", "content": "data\n"})

    assert "File created successfully" in result
    assert (workspace / "nested" / "dir" / "a.txt").read_text(encoding="utf-8") == "data\n"


def test_write_overwrites_existing_file(workspace: Path) -> None:
    path = workspace / "a.txt"
    path.write_text("original\n", encoding="utf-8")

    result = WRITE.execute({"file_path": "a.txt", "content": "replacement\n"})

    assert "has been updated successfully" in result
    assert path.read_text(encoding="utf-8") == "replacement\n"


def test_write_keeps_content_line_endings_verbatim(workspace: Path) -> None:
    WRITE.execute({"file_path": "a.txt", "content": "a\r\nb\n"})

    assert (workspace / "a.txt").read_bytes() == b"a\r\nb\n"
