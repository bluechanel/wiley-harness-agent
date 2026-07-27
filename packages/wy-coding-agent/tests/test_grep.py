import os
from pathlib import Path

import pytest

from wy_coding_agent.tools.grep import GrepToolError, execute_grep


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_files_with_matches_sorts_by_mtime(workspace: Path) -> None:
    (workspace / "old.txt").write_text("needle\n", encoding="utf-8")
    (workspace / "new.txt").write_text("needle\n", encoding="utf-8")
    os.utime(workspace / "old.txt", (1_000, 1_000))
    os.utime(workspace / "new.txt", (2_000, 2_000))

    result = execute_grep({"pattern": "needle"})

    assert result == "Found 2 files\nnew.txt\nold.txt"


def test_content_mode_shows_line_numbers_and_context(workspace: Path) -> None:
    (workspace / "a.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    result = execute_grep({"pattern": "two", "output_mode": "content", "-C": 1})

    assert result == "a.txt-1-one\na.txt:2:two\na.txt-3-three"


def test_content_mode_separates_context_groups(workspace: Path) -> None:
    lines = "match\n" + "gap\n" * 5 + "match\n"
    (workspace / "a.txt").write_text(lines, encoding="utf-8")

    result = execute_grep({"pattern": "match", "output_mode": "content", "-A": 1})

    assert result == "a.txt:1:match\na.txt-2-gap\n--\na.txt:7:match"


def test_content_mode_without_line_numbers(workspace: Path) -> None:
    (workspace / "a.txt").write_text("hello\n", encoding="utf-8")

    result = execute_grep({"pattern": "hello", "output_mode": "content", "-n": False})

    assert result == "a.txt:hello"


def test_count_mode_totals(workspace: Path) -> None:
    (workspace / "a.txt").write_text("x\nx\n", encoding="utf-8")
    (workspace / "b.txt").write_text("x\n", encoding="utf-8")

    result = execute_grep({"pattern": "x", "output_mode": "count"})

    assert "a.txt:2" in result
    assert "b.txt:1" in result
    assert result.endswith("Found 3 total occurrences across 2 files.")


def test_case_insensitive_flag(workspace: Path) -> None:
    (workspace / "a.txt").write_text("HELLO\n", encoding="utf-8")

    assert execute_grep({"pattern": "hello"}) == "No files found"
    assert execute_grep({"pattern": "hello", "-i": True}) == "Found 1 file\na.txt"


def test_glob_filter_with_brace_expansion(workspace: Path) -> None:
    (workspace / "a.py").write_text("needle\n", encoding="utf-8")
    (workspace / "b.md").write_text("needle\n", encoding="utf-8")
    (workspace / "c.txt").write_text("needle\n", encoding="utf-8")

    result = execute_grep({"pattern": "needle", "glob": "*.{md,txt}"})

    assert "b.md" in result and "c.txt" in result
    assert "a.py" not in result


def test_negated_glob_excludes_files(workspace: Path) -> None:
    (workspace / "a.py").write_text("needle\n", encoding="utf-8")
    (workspace / "a_test.py").write_text("needle\n", encoding="utf-8")

    result = execute_grep({"pattern": "needle", "glob": "*.py !*_test.py"})

    assert result == "Found 1 file\na.py"


def test_type_filter(workspace: Path) -> None:
    (workspace / "a.py").write_text("needle\n", encoding="utf-8")
    (workspace / "b.txt").write_text("needle\n", encoding="utf-8")

    assert execute_grep({"pattern": "needle", "type": "py"}) == "Found 1 file\na.py"

    with pytest.raises(GrepToolError, match="file type"):
        execute_grep({"pattern": "needle", "type": "nope"})


def test_head_limit_and_offset_pagination(workspace: Path) -> None:
    for index in range(5):
        path = workspace / f"f{index}.txt"
        path.write_text("needle\n", encoding="utf-8")
        os.utime(path, (1_000 + index, 1_000 + index))

    limited = execute_grep({"pattern": "needle", "head_limit": 2})
    assert limited == "Found 2 files limit: 2\nf4.txt\nf3.txt"

    tail = execute_grep({"pattern": "needle", "head_limit": 2, "offset": 4})
    assert tail == "Found 1 file offset: 4\nf0.txt"


def test_content_mode_pagination_note(workspace: Path) -> None:
    (workspace / "a.txt").write_text("x\nx\nx\n", encoding="utf-8")

    result = execute_grep({"pattern": "x", "output_mode": "content", "head_limit": 2})

    assert result.endswith("[Showing results with pagination = limit: 2]")
    assert result.count("a.txt:") == 2


def test_no_matches(workspace: Path) -> None:
    (workspace / "a.txt").write_text("hello\n", encoding="utf-8")

    assert execute_grep({"pattern": "zzz"}) == "No files found"
    assert (
        execute_grep({"pattern": "zzz", "output_mode": "content"}) == "No matches found"
    )


def test_vcs_directories_are_excluded(workspace: Path) -> None:
    (workspace / ".git").mkdir()
    (workspace / ".git" / "hit.txt").write_text("needle\n", encoding="utf-8")
    (workspace / "real.txt").write_text("needle\n", encoding="utf-8")

    assert execute_grep({"pattern": "needle"}) == "Found 1 file\nreal.txt"


def test_gitignore_is_respected_in_git_repos(workspace: Path) -> None:
    (workspace / ".git").mkdir()  # rg only applies .gitignore inside a repo
    (workspace / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (workspace / "ignored.txt").write_text("needle\n", encoding="utf-8")
    (workspace / "real.txt").write_text("needle\n", encoding="utf-8")

    assert execute_grep({"pattern": "needle"}) == "Found 1 file\nreal.txt"


def test_multiline_mode_spans_lines(workspace: Path) -> None:
    (workspace / "a.txt").write_text("foo\nbar\n", encoding="utf-8")

    assert execute_grep({"pattern": "foo.bar"}) == "No files found"
    assert (
        execute_grep({"pattern": "foo.bar", "multiline": True, "output_mode": "content"})
        == "a.txt:1:foo\na.txt:2:bar"
    )


def test_binary_files_are_skipped(workspace: Path) -> None:
    (workspace / "blob.bin").write_bytes(b"needle\x00needle")
    (workspace / "text.txt").write_text("needle\n", encoding="utf-8")

    assert execute_grep({"pattern": "needle"}) == "Found 1 file\ntext.txt"


def test_explicit_single_file_path(workspace: Path) -> None:
    (workspace / "a.txt").write_text("needle\n", encoding="utf-8")

    result = execute_grep({"pattern": "needle", "path": "a.txt", "output_mode": "content"})

    assert result == "a.txt:1:needle"


def test_invalid_arguments_raise(workspace: Path) -> None:
    with pytest.raises(GrepToolError, match="Missing required argument: pattern"):
        execute_grep({})
    with pytest.raises(GrepToolError, match="regex parse error"):
        execute_grep({"pattern": "("})
    with pytest.raises(GrepToolError, match="Path does not exist"):
        execute_grep({"pattern": "x", "path": "missing_dir"})
    with pytest.raises(GrepToolError, match="Unsupported output_mode"):
        execute_grep({"pattern": "x", "output_mode": "lines"})
    with pytest.raises(GrepToolError, match="head_limit"):
        execute_grep({"pattern": "x", "head_limit": -1})


def test_long_lines_are_truncated_in_display(workspace: Path) -> None:
    (workspace / "a.txt").write_text("needle" + "x" * 1_000 + "\n", encoding="utf-8")

    result = execute_grep({"pattern": "needle", "output_mode": "content"})

    assert "Omitted long" in result  # rg --max-columns replaces oversized lines
    assert len(result) < 700
