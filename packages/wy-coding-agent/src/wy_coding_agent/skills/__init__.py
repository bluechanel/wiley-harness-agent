"""Agent Skills integration: discovery, rendering + ``SkillTool`` executor.

``discover_skills`` finds SKILL.md directories, ``render_skill`` loads and
renders skill bodies on demand, and ``SkillTool`` wraps the result as a
``wy_core.Tool`` for the agent loop.  Re-export everything from this package
so callers can import from ``wy_coding_agent.skills``.
"""

from wy_coding_agent.skills.discovery import (
    SKILL_FILE,
    Skill,
    default_skills_dirs,
    discover_skills,
    parse_frontmatter,
    render_skill,
)
from wy_coding_agent.skills.tool import SkillTool

__all__ = [
    "SKILL_FILE",
    "Skill",
    "SkillTool",
    "default_skills_dirs",
    "discover_skills",
    "parse_frontmatter",
    "render_skill",
]
