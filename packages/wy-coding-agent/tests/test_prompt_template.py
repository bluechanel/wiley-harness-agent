from pathlib import Path

from wy_coding_agent.prompt_template import (
    AgentMDProvider,
    BasePromptProvider,
    DeferredToolProvider,
    MemoryProvider,
    ModelProvider,
    SkillProvider,
    WorkspaceProvider,
    build_prompt,
    default_prompt_providers,
)
from wy_coding_agent.skills import Skill


class _StaticProvider(BasePromptProvider):
    def __init__(self, section: str | None) -> None:
        self._section = section

    def provide(self) -> str | None:
        return self._section


def test_build_prompt_strips_instruction() -> None:
    assert build_prompt("  be brief  ") == "be brief"


def test_build_prompt_empty_returns_none() -> None:
    assert build_prompt(None) is None
    assert build_prompt("   ") is None
    assert build_prompt(None, (_StaticProvider(None), _StaticProvider("  "))) is None


def test_build_prompt_composes_sections_in_order() -> None:
    providers = (
        _StaticProvider("# One\n\nfirst"),
        _StaticProvider(None),
        _StaticProvider("# Two\n\nsecond"),
    )
    assert build_prompt("be brief", providers) == (
        "be brief\n\n# One\n\nfirst\n\n# Two\n\nsecond"
    )


def test_model_provider() -> None:
    assert ModelProvider("model-x").provide() == (
        "# Model\n\nYou are powered by the model `model-x`."
    )
    assert ModelProvider("   ").provide() is None


def test_workspace_provider(tmp_path: Path) -> None:
    section = WorkspaceProvider(tmp_path).provide()
    assert section is not None
    assert section.startswith("# Workspace")
    assert f"- Working directory: {tmp_path}" in section
    assert "- Is a git repository: no" in section

    (tmp_path / ".git").mkdir()
    assert "- Is a git repository: yes" in WorkspaceProvider(tmp_path).provide()


def test_agent_md_provider_prefers_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("agents rules", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("claude rules", encoding="utf-8")
    assert AgentMDProvider(tmp_path).provide() == (
        "# Project instructions (AGENTS.md)\n\nagents rules"
    )


def test_agent_md_provider_falls_back_to_claude_md(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("claude rules", encoding="utf-8")
    assert AgentMDProvider(tmp_path).provide() == (
        "# Project instructions (CLAUDE.md)\n\nclaude rules"
    )


def test_agent_md_provider_missing_returns_none(tmp_path: Path) -> None:
    assert AgentMDProvider(tmp_path).provide() is None


def test_skill_provider_lists_discovered_skills() -> None:
    skills = (
        Skill(name="commit", description="Write commits", directory=Path("/s/commit")),
        Skill(name="deploy", description="Ship the app", directory=Path("/s/deploy")),
    )
    section = SkillProvider(skills).provide()
    assert section is not None
    assert section.startswith("# Skills")
    assert "invoke the `skill` tool" in section
    assert "- commit: Write commits" in section
    assert "- deploy: Ship the app" in section


def test_skill_provider_hides_unlisted_and_empty() -> None:
    assert SkillProvider(()).provide() is None
    unlisted = Skill(
        name="manual", description="d", directory=Path("/s/manual"), listed=False
    )
    assert SkillProvider((unlisted,)).provide() is None


def test_memory_provider(tmp_path: Path) -> None:
    memory_path = tmp_path / "MEMORY.md"
    assert MemoryProvider(memory_path).provide() is None

    memory_path.write_text("remember this", encoding="utf-8")
    assert MemoryProvider(memory_path).provide() == "# Memory\n\nremember this"


def test_unreadable_file_returns_none(tmp_path: Path) -> None:
    memory_path = tmp_path / "MEMORY.md"
    memory_path.write_bytes(b"\xff\xfe\xff")
    assert MemoryProvider(memory_path).provide() is None


def test_deferred_tool_provider() -> None:
    assert DeferredToolProvider(()).provide() is None

    section = DeferredToolProvider(("mcp__demo__add", "mcp__demo__boom")).provide()
    assert section is not None
    assert section.startswith("# Deferred tools")
    assert "tool_search" in section
    assert "- mcp__demo__add" in section
    assert "- mcp__demo__boom" in section


def test_default_prompt_providers_order(tmp_path: Path) -> None:
    providers = default_prompt_providers("model-x", tmp_path)
    assert [type(provider) for provider in providers] == [
        ModelProvider,
        WorkspaceProvider,
        AgentMDProvider,
        SkillProvider,
        DeferredToolProvider,
        MemoryProvider,
    ]
