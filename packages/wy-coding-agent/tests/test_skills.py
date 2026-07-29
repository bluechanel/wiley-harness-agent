"""Skills:SKILL.md 发现、frontmatter 解析、渲染与 skill 工具。"""

from pathlib import Path

import pytest

from wy_coding_agent.skills import (
    Skill,
    default_skills_dirs,
    discover_skills,
    parse_frontmatter,
    render_skill,
)
from wy_coding_agent.tools.skill import SkillTool


def make_skill(base: Path, name: str, text: str) -> Path:
    directory = base / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(text, encoding="utf-8")
    return directory


DEPLOY = """---
name: deploy
description: Deploy the app. Use when the user asks to ship or deploy.
---

# Deploy

Run the deploy script in ${CLAUDE_SKILL_DIR}/scripts.
Target: $ARGUMENTS
"""


def test_parse_frontmatter_fields_and_body() -> None:
    fields, body = parse_frontmatter(DEPLOY)
    assert fields["name"] == "deploy"
    assert fields["description"].startswith("Deploy the app.")
    assert body.strip().startswith("# Deploy")


def test_parse_frontmatter_quoted_and_block_scalar() -> None:
    fields, _ = parse_frontmatter(
        "---\n"
        'name: "quoted"\n'
        "description: >-\n"
        "  first line\n"
        "  second line\n"
        "allowed-tools:\n"
        "  - Read\n"
        "---\n"
        "body\n"
    )
    assert fields["name"] == "quoted"
    assert fields["description"] == "first line second line"
    assert fields.get("allowed-tools") == ""  # 列表值不解析,留空即忽略


def test_parse_frontmatter_missing_or_unclosed_is_body() -> None:
    assert parse_frontmatter("just body") == ({}, "just body")
    fields, body = parse_frontmatter("---\nname: x\nno closing")
    assert fields == {}
    assert body == "---\nname: x\nno closing"


def test_discover_reads_metadata(tmp_path: Path) -> None:
    make_skill(tmp_path, "deploy", DEPLOY)
    skills = discover_skills((tmp_path,))
    assert [s.name for s in skills] == ["deploy"]
    assert skills[0].description.startswith("Deploy the app.")
    assert skills[0].directory == tmp_path / "deploy"
    assert skills[0].listed is True


def test_discover_description_falls_back_to_first_paragraph(tmp_path: Path) -> None:
    make_skill(tmp_path, "notes", "# Notes\n\nKeep notes tidy.\nAlways.\n")
    skills = discover_skills((tmp_path,))
    assert skills[0].description == "Keep notes tidy. Always."


def test_discover_skips_invalid_entries(tmp_path: Path) -> None:
    (tmp_path / "not-a-skill").mkdir()  # 无 SKILL.md
    make_skill(tmp_path, "empty", "")  # 无 description 且正文为空
    (tmp_path / "loose.md").write_text("x", encoding="utf-8")  # 平铺文件
    assert discover_skills((tmp_path,)) == ()
    assert discover_skills((tmp_path / "missing",)) == ()


def test_discover_first_dir_wins_on_name_clash(tmp_path: Path) -> None:
    personal, project = tmp_path / "personal", tmp_path / "project"
    make_skill(personal, "deploy", "---\ndescription: personal one\n---\nbody")
    make_skill(project, "deploy", "---\ndescription: project one\n---\nbody")
    make_skill(project, "extra", "---\ndescription: extra one\n---\nbody")
    skills = discover_skills((personal, project))
    assert {s.name: s.description for s in skills} == {
        "deploy": "personal one",
        "extra": "extra one",
    }


def test_discover_disable_model_invocation_unlists(tmp_path: Path) -> None:
    make_skill(
        tmp_path,
        "manual",
        "---\ndescription: manual only\ndisable-model-invocation: true\n---\nbody",
    )
    skills = discover_skills((tmp_path,))
    assert skills[0].listed is False


def test_render_substitutes_placeholders(tmp_path: Path) -> None:
    directory = make_skill(tmp_path, "deploy", DEPLOY)
    skill = discover_skills((tmp_path,))[0]
    rendered = render_skill(skill, "staging")
    assert f'Loaded skill "deploy" (directory: {directory})' in rendered
    assert f"{directory}/scripts" in rendered
    assert "Target: staging" in rendered
    assert "$ARGUMENTS" not in rendered
    assert "${CLAUDE_SKILL_DIR}" not in rendered


def test_render_appends_arguments_without_placeholder(tmp_path: Path) -> None:
    make_skill(tmp_path, "plain", "---\ndescription: d\n---\nDo the thing.")
    skill = discover_skills((tmp_path,))[0]
    assert render_skill(skill, "extra input").endswith("ARGUMENTS: extra input\n")
    assert "ARGUMENTS:" not in render_skill(skill)


def test_render_rereads_file(tmp_path: Path) -> None:
    directory = make_skill(tmp_path, "live", "---\ndescription: d\n---\nold body")
    skill = discover_skills((tmp_path,))[0]
    (directory / "SKILL.md").write_text(
        "---\ndescription: d\n---\nnew body", encoding="utf-8"
    )
    assert "new body" in render_skill(skill)


def test_default_skills_dirs(tmp_path: Path) -> None:
    assert default_skills_dirs(tmp_path) == (
        Path.home() / ".claude" / "skills",
        tmp_path / ".claude" / "skills",
    )


def test_skill_tool_executes_and_strips_slash(tmp_path: Path) -> None:
    make_skill(tmp_path, "deploy", DEPLOY)
    tool = SkillTool(discover_skills((tmp_path,)))
    assert tool.name == "skill"
    rendered = tool.execute({"skill": "/deploy", "args": "prod"})
    assert "Target: prod" in rendered


def test_skill_tool_unknown_skill_raises(tmp_path: Path) -> None:
    make_skill(tmp_path, "deploy", DEPLOY)
    tool = SkillTool(discover_skills((tmp_path,)))
    with pytest.raises(RuntimeError, match="available: deploy"):
        tool.execute({"skill": "nope"})


def test_skill_tool_serves_unlisted_skills(tmp_path: Path) -> None:
    make_skill(
        tmp_path,
        "manual",
        "---\ndescription: manual only\ndisable-model-invocation: true\n---\nsecret steps",
    )
    tool = SkillTool(discover_skills((tmp_path,)))
    assert "secret steps" in tool.execute({"skill": "manual"})


def test_skill_dataclass_defaults() -> None:
    skill = Skill(name="x", description="d", directory=Path("/tmp/x"))
    assert skill.listed is True
