"""reminders 层:claudeMd 上下文注入与 HarnessState。"""

from pathlib import Path

from wy_coding_agent.reminders import ClaudeMdReminderProvider, HarnessState


def _provider(workspace: Path, home: Path) -> ClaudeMdReminderProvider:
    return ClaudeMdReminderProvider(workspace=workspace, home=home)


def test_claude_md_all_missing_returns_current_date_only(tmp_path: Path) -> None:
    text = _provider(tmp_path, tmp_path / "home").provide()
    assert text is not None
    assert "# claudeMd" not in text
    assert text.startswith("# currentDate")
    assert "IMPORTANT" in text


def test_claude_md_injects_global_project_memory(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text("global rules", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agents rules", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("remember this", encoding="utf-8")

    text = _provider(tmp_path, home).provide()
    assert text is not None
    assert text.startswith("# claudeMd")
    assert (
        f"Contents of {home / '.claude' / 'CLAUDE.md'} "
        "(user's private global instructions for all projects):"
    ) in text
    assert (
        f"Contents of {tmp_path / 'AGENTS.md'} "
        "(project instructions, checked into the codebase):"
    ) in text
    assert (
        f"Contents of {tmp_path / 'MEMORY.md'} "
        "(user's auto-memory, persists across conversations):"
    ) in text
    assert "global rules" in text
    assert "agents rules" in text
    assert "remember this" in text
    assert "# currentDate" in text
    assert "IMPORTANT" in text


def test_claude_md_项目指令_AGENTS优先回落CLAUDE(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("project-agents", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("project-claude", encoding="utf-8")
    text = _provider(tmp_path, home).provide()
    assert text is not None
    assert "project-agents" in text
    assert "project-claude" not in text  # AGENTS.md 优先,CLAUDE.md 不注入

    (tmp_path / "AGENTS.md").unlink()
    text = _provider(tmp_path, home).provide()
    assert text is not None
    assert "project-claude" in text


def test_claude_md_不可读文件跳过_不raise(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_bytes(b"\xff\xfe\xff")
    text = _provider(tmp_path, home).provide()  # 不 raise
    assert text is not None
    assert text.startswith("# currentDate")


def test_harness_state_快照与恢复() -> None:
    state = HarnessState()
    state.enable_plan()
    assert state.snapshot() == {"plan_active": True}

    fresh = HarnessState()
    fresh.restore({"plan_active": True})
    assert fresh.plan_active
