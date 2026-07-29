"""Skill 工具执行器:把已发现的 Agent Skills 按需加载进上下文(L2)。

与 ``mcp_tool`` 同型:本模块只有类、无模块级实例,自动扫描不收录;由
factory 按 ``discover_skills`` 的结果构造。可用 skills 的清单(L1)由
``prompt_template.SkillProvider`` 注入系统提示,本工具描述保持通用。
"""

from collections.abc import Sequence

from wy_core import Tool

from wy_coding_agent.skills import Skill, render_skill


class SkillTool(Tool):
    """Load a discovered agent skill's full instructions on demand."""

    name = "skill"
    description = (
        "Invoke an agent skill: load its full instructions into context, then "
        "follow them for the current task.\n"
        "- Available skills are listed with their descriptions in the Skills "
        "section of the system prompt; invoke the matching skill before "
        "relying on anything it covers\n"
        '- When the user types "/<name> [args]" they are requesting that '
        "skill: invoke it with the trailing text as args\n"
        "- The result states the skill directory; load files it references "
        "with the read/glob tools only when needed"
    )
    parameters = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "Name of the skill to load.",
            },
            "args": {
                "type": "string",
                "description": (
                    "Optional arguments for the skill, substituted into its "
                    "$ARGUMENTS placeholder."
                ),
            },
        },
        "required": ["skill"],
        "additionalProperties": False,
    }

    def __init__(self, skills: Sequence[Skill]) -> None:
        self._skills = {skill.name: skill for skill in skills}

    def execute(self, input: dict) -> str:
        name = str(input.get("skill", "")).strip().lstrip("/")
        skill = self._skills.get(name)
        if skill is None:
            known = ", ".join(sorted(self._skills)) or "(none)"
            raise RuntimeError(f"unknown skill {name!r}; available: {known}")
        args = input.get("args")
        return render_skill(skill, str(args) if args is not None else "")
