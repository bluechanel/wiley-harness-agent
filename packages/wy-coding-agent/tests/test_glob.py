import os
from pathlib import Path

import pytest

from wy_coding_agent.tools.glob import GLOB, GlobToolError


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_glob_matches_by_name_at_any_depth(workspace: Path) -> None:
    (workspace / "a.py").write_text("x\n", encoding="utf-8")
    (workspace / "sub").mkdir()
    (workspace / "sub" / "b.py").write_text("x\n", encoding="utf-8")
    (workspace / "c.txt").write_text("x\n", encoding="utf-8")

    result = GLOB.execute({"pattern": "*.py"})

    assert set(result.splitlines()) == {"a.py", "sub/b.py"}
    # "**" spans zero or more directories, so root-level files match too.
    assert set(GLOB.execute({"pattern": "**/*.py"}).splitlines()) == {"a.py", "sub/b.py"}


def test_glob_sorts_by_mtime_most_recent_first(workspace: Path) -> None:
    (workspace / "old.txt").write_text("x\n", encoding="utf-8")
    (workspace / "new.txt").write_text("x\n", encoding="utf-8")
    os.utime(workspace / "old.txt", (1_000, 1_000))
    os.utime(workspace / "new.txt", (2_000, 2_000))

    assert GLOB.execute({"pattern": "*.txt"}) == "new.txt\nold.txt"


def test_glob_recursive_pattern_with_directory_prefix(workspace: Path) -> None:
    (workspace / "src" / "deep").mkdir(parents=True)
    (workspace / "src" / "deep" / "a.ts").write_text("x\n", encoding="utf-8")
    (workspace / "b.ts").write_text("x\n", encoding="utf-8")

    assert GLOB.execute({"pattern": "src/**/*.ts"}) == "src/deep/a.ts"


def test_glob_explicit_path_scopes_the_search(workspace: Path) -> None:
    (workspace / "sub").mkdir()
    (workspace / "sub" / "a.md").write_text("x\n", encoding="utf-8")
    (workspace / "b.md").write_text("x\n", encoding="utf-8")

    assert GLOB.execute({"pattern": "*.md", "path": "sub"}) == "sub/a.md"


def test_glob_no_matches(workspace: Path) -> None:
    (workspace / "a.txt").write_text("x\n", encoding="utf-8")

    assert GLOB.execute({"pattern": "*.zig"}) == "No files found"


def test_glob_respects_gitignore_and_skips_vcs_dirs(workspace: Path) -> None:
    (workspace / ".git").mkdir()  # rg only applies .gitignore inside a repo
    (workspace / ".git" / "hidden.txt").write_text("x\n", encoding="utf-8")
    (workspace / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (workspace / "ignored.txt").write_text("x\n", encoding="utf-8")
    (workspace / "real.txt").write_text("x\n", encoding="utf-8")

    assert GLOB.execute({"pattern": "*.txt"}) == "real.txt"


def test_glob_includes_hidden_files(workspace: Path) -> None:
    (workspace / ".hidden.cfg").write_text("x\n", encoding="utf-8")

    assert GLOB.execute({"pattern": "*.cfg"}) == ".hidden.cfg"


def test_glob_truncates_at_100_files(workspace: Path) -> None:
    for index in range(105):
        (workspace / f"f{index:03}.txt").write_text("x\n", encoding="utf-8")

    lines = GLOB.execute({"pattern": "*.txt"}).splitlines()

    assert len(lines) == 101
    assert lines[-1] == (
        "(Results are truncated. Consider using a more specific path or pattern.)"
    )


def test_glob_invalid_arguments_raise(workspace: Path) -> None:
    with pytest.raises(GlobToolError, match="Missing required argument: pattern"):
        GLOB.execute({})
    with pytest.raises(GlobToolError, match="Directory does not exist"):
        GLOB.execute({"pattern": "*.py", "path": "missing_dir"})
    (workspace / "file.txt").write_text("x\n", encoding="utf-8")
    with pytest.raises(GlobToolError, match="not a directory"):
        GLOB.execute({"pattern": "*.py", "path": "file.txt"})
