"""Agent Skills:SKILL.md 技能包的发现、解析与渲染。

对齐 Anthropic Agent Skills 规范(agentskills.io / 官方文档)的应用侧实现:
每个 skill 是一个含 ``SKILL.md`` 的目录(frontmatter 元数据 + 正文指令 +
任意随包文件),按三级渐进披露进入上下文——系统提示只列 name+description
(L1,`prompt_template.SkillProvider`),模型经 ``skill`` 工具加载正文
(L2,`tools/skill.py`),随包文件由模型用 read/glob/bash 工具按需取用
(L3)。本模块只负责发现与渲染,不做任何 I/O 之外的策略。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SKILL_FILE = "SKILL.md"
_DESCRIPTION_LIMIT = 1536  # 官方对清单内 description 的截断上限
_KEY_VALUE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$")
_TRUE_VALUES = frozenset({"true", "yes", "on", "1"})


@dataclass(frozen=True, slots=True)
class Skill:
    """一个已发现的 skill;name 即调用名,取目录名(对齐 Claude Code)。

    ``listed=False``(frontmatter ``disable-model-invocation: true``)的
    skill 不进系统提示清单,但仍可经 ``skill`` 工具调用,供用户以
    ``/name`` 显式触发。
    """

    name: str
    description: str
    directory: Path
    listed: bool = True


def default_skills_dirs(workspace: Path | None = None) -> tuple[Path, ...]:
    """官方默认目录:个人 ``~/.claude/skills`` 优先于项目 ``.claude/skills``。"""
    root = (workspace or Path.cwd()).expanduser()
    return (Path.home() / ".claude" / "skills", root / ".claude" / "skills")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析文首 ``---`` 包围的 frontmatter,返回 (字段表, 正文)。

    手写 YAML 子集,不引入 yaml 依赖:仅顶层 ``key: value``(裸值、引号值
    与 ``>``/``|`` 块标量);列表与嵌套结构一律跳过。无 frontmatter 或未
    闭合时整个文本视为正文。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    close = next(
        (i for i in range(1, len(lines)) if lines[i].strip() == "---"), None
    )
    if close is None:
        return {}, text

    fields: dict[str, str] = {}
    index = 1
    while index < close:
        match = _KEY_VALUE.match(lines[index])
        if match is None:
            index += 1
            continue
        key, value = match.group(1), match.group(2).strip()
        if value in ("|", "|-", ">", ">-"):
            block: list[str] = []
            index += 1
            while index < close and (
                not lines[index].strip() or lines[index][:1] in (" ", "\t")
            ):
                if lines[index].strip():
                    block.append(lines[index].strip())
                index += 1
            fields[key] = (" " if value.startswith(">") else "\n").join(block)
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        fields[key] = value
        index += 1
    return fields, "\n".join(lines[close + 1 :])


def _fallback_description(body: str) -> str:
    """frontmatter 缺 description 时取正文首个非标题段落(对齐 Claude Code)。"""
    for paragraph in re.split(r"\n[ \t]*\n", body):
        text = " ".join(
            line.strip()
            for line in paragraph.splitlines()
            if not line.lstrip().startswith("#")
        )
        text = " ".join(text.split())
        if text:
            return text
    return ""


def _load_skill(directory: Path) -> Skill | None:
    fields, body = parse_frontmatter(
        (directory / SKILL_FILE).read_text(encoding="utf-8")
    )
    description = " ".join(fields.get("description", "").split())
    description = description or _fallback_description(body)
    if not description:
        logger.warning("skill %s 缺少 description 且正文为空,已跳过", directory)
        return None
    listed = (
        fields.get("disable-model-invocation", "").strip().lower()
        not in _TRUE_VALUES
    )
    return Skill(
        name=directory.name,
        description=description[:_DESCRIPTION_LIMIT],
        directory=directory,
        listed=listed,
    )


def discover_skills(dirs: Sequence[Path]) -> tuple[Skill, ...]:
    """扫描各目录下的 ``<name>/SKILL.md``;目录顺序即优先级,同名先者胜。

    目录缺失/不可读整体跳过;单个 skill 解析失败记 warning 跳过,不阻断
    agent 启动。
    """
    skills: dict[str, Skill] = {}
    for base in dirs:
        base = Path(base).expanduser()
        try:
            entries = sorted(p for p in base.iterdir() if (p / SKILL_FILE).is_file())
        except OSError:
            continue
        for entry in entries:
            if entry.name in skills:
                continue
            try:
                skill = _load_skill(entry)
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("加载 skill %s 失败:%s", entry, exc)
                continue
            if skill is not None:
                skills[entry.name] = skill
    return tuple(skills.values())


def render_skill(skill: Skill, args: str = "") -> str:
    """加载并渲染 skill 正文(L2):替换占位符并附使用说明头。

    每次调用重读 SKILL.md,编辑即时生效;读取失败直接 raise,由 agent
    循环统一转工具错误。替换规则对齐官方:``$ARGUMENTS`` 换成调用参数
    (无占位符且有参数时在文末追加 ``ARGUMENTS: <args>``),
    ``${CLAUDE_SKILL_DIR}`` 换成 skill 目录,便于复用现有 Claude skills。
    """
    _, body = parse_frontmatter(
        (skill.directory / SKILL_FILE).read_text(encoding="utf-8")
    )
    body = body.replace("${CLAUDE_SKILL_DIR}", str(skill.directory))
    if "$ARGUMENTS" in body:
        body = body.replace("$ARGUMENTS", args)
    elif args:
        body = f"{body.rstrip()}\n\nARGUMENTS: {args}"
    header = (
        f'Loaded skill "{skill.name}" (directory: {skill.directory}).\n'
        "Follow these instructions for the current task. Relative paths below "
        "resolve against the skill directory: read referenced files with the "
        "read/glob tools and run referenced scripts with the bash tool, only "
        "when needed."
    )
    return f"{header}\n\n{body.strip()}\n"
