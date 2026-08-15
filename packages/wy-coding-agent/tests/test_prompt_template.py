from datetime import date
from pathlib import Path
import platform

from wy_coding_agent.prompt_template import (
    AgentMDProvider,
    ContextManagementProvider,
    DeferredToolProvider,
    EnvironmentProvider,
    HarnessProvider,
    IdentityProvider,
    MemoryProvider,
    PlanModeProvider,
    SessionGuidanceProvider,
    SkillProvider,
    TextProvider,
    _TemplateProvider,
    build_prompt,
    build_prompt_context,
    default_instruction_providers,
    default_prompt_providers,
)
from wy_coding_agent.reminders import HarnessState
from wy_coding_agent.skills import Skill


def test_text_provider_strips() -> None:
    assert TextProvider("  be brief  ").provide() == "be brief"
    assert TextProvider("   ").provide() is None


def test_build_prompt_empty_returns_none() -> None:
    assert build_prompt() is None
    assert build_prompt((TextProvider(""), TextProvider("  "))) is None


def test_build_prompt_composes_sections_in_order() -> None:
    providers = (
        TextProvider("be brief"),
        TextProvider("# One\n\nfirst"),
        TextProvider(""),
        TextProvider("# Two\n\nsecond"),
    )
    assert build_prompt(providers) == (
        "be brief\n\n# One\n\nfirst\n\n# Two\n\nsecond"
    )


def test_build_prompt_context(tmp_path: Path) -> None:
    ctx = build_prompt_context("model-x", tmp_path)
    assert ctx.workspace == tmp_path
    assert ctx.model == "model-x"
    assert ctx.is_git is False
    assert ctx.platform == platform.system()
    assert ctx.date == date.today()

    (tmp_path / ".git").mkdir()
    assert build_prompt_context("model-x", tmp_path).is_git is True


def test_environment_provider_renders_placeholders(tmp_path: Path) -> None:
    ctx = build_prompt_context("model-x", tmp_path)
    section = EnvironmentProvider(ctx).provide()
    assert section is not None
    assert section.startswith("# Environment")
    assert f"- Working directory: {tmp_path}" in section
    assert "- Is a git repository: no" in section
    assert f"- Platform: {platform.system()}" in section
    assert f"- Today's date: {date.today().isoformat()}" in section
    assert "- Model: model-x" in section


def test_template_providers_return_content(tmp_path: Path) -> None:
    ctx = build_prompt_context("model-x", tmp_path)
    assert IdentityProvider(ctx).provide().startswith("You are")
    assert HarnessProvider(ctx).provide().startswith("# Harness")
    assert SessionGuidanceProvider(ctx).provide().startswith(
        "# Session-specific guidance"
    )
    assert ContextManagementProvider(ctx).provide().startswith("# Context management")


def test_template_provider_unknown_placeholder_falls_back(tmp_path: Path) -> None:
    ctx = build_prompt_context("model-x", tmp_path)

    class _BrokenTemplate(_TemplateProvider):
        _TEMPLATE = "{missing} text"

    assert _BrokenTemplate(ctx).provide() == "{missing} text"


def test_default_instruction_providers_order(tmp_path: Path) -> None:
    providers = default_instruction_providers(build_prompt_context("model-x", tmp_path))
    assert [type(provider) for provider in providers] == [
        IdentityProvider,
        HarnessProvider,
        SessionGuidanceProvider,
        EnvironmentProvider,
        ContextManagementProvider,
    ]


def test_build_prompt_orders_template_before_custom(tmp_path: Path) -> None:
    ctx = build_prompt_context("model-x", tmp_path)
    prompt = build_prompt((IdentityProvider(ctx), TextProvider("custom")))
    assert prompt is not None
    assert prompt.startswith("You are")
    assert prompt.endswith("\n\ncustom")


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


def test_default_prompt_providers_order() -> None:
    providers = default_prompt_providers()
    assert [type(provider) for provider in providers] == [
        SkillProvider,
        DeferredToolProvider,
    ]


def test_plan_mode_provider() -> None:
    section = PlanModeProvider().provide()
    assert section is not None
    assert section.startswith("# Plan mode")
    assert "exit_plan_mode" in section
    for heading in (
        "# Skills",
        "# Deferred tools",
        "# Memory",
        "# Project instructions",
        "# currentDate",
    ):
        assert heading not in section


def test_build_prompt_harness_plan_active() -> None:
    harness = HarnessState()
    base = build_prompt((TextProvider("hello"),))
    assert base == "hello"
    # plan 未激活或 harness=None 时不含 plan 段
    assert build_prompt((TextProvider("hello"),), harness=harness) == "hello"
    assert build_prompt((TextProvider("hello"),), harness=None) == "hello"

    harness.enable_plan()
    prompt = build_prompt((TextProvider("hello"),), harness=harness)
    assert prompt is not None
    assert prompt.startswith("hello")
    assert "# Plan mode" in prompt
    assert "exit_plan_mode" in prompt
