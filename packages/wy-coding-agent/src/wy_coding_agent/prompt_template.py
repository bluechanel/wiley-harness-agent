"""Composable prompt providers that assemble the system prompt.

每个 `BasePromptProvider` 子类贡献 system prompt 的一个分节；`build_prompt`
按 harness 状态把各 provider 分节组合成最终提示词（每次提交 LLM 前由
`system_builder` 调用）。分节来源：

- 静态指令分节（`IdentityProvider`/`HarnessProvider`/`SessionGuidanceProvider`/
  `EnvironmentProvider`/`ContextManagementProvider`）：模板写在
  `prompt_sections.py` 的字符串常量里，支持 `{占位符}` 动态注入——
  占位符键由 `PromptContext.as_dict()` 定义，经 `str.format` 渲染。
- 条件分节（`PlanModeProvider`）：harness 状态 `plan_active` 时由
  `build_prompt(..., harness=...)` 尾追注入。
- 动态分节（`SkillProvider`/`DeferredToolProvider` 与 `TextProvider`）：
  携带运行时数据（发现的 skill、懒加载工具名、调用方自定义文本）。
  项目指令（AGENTS.md/CLAUDE.md）与 MEMORY.md 已移入
  `reminders.ClaudeMdReminderProvider`（每回合注入）；`AgentMDProvider`/
  `MemoryProvider` 类保留，供自定义链显式加回。

`default_instruction_providers(context)` 给出静态指令段（排头），
`default_prompt_providers(skills, deferred_tools)` 给出动态尾；`create_agent`
把两者拼成一条链，交由每次提交时调用的 `system_builder`。
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import platform
from typing import Protocol

from wy_coding_agent.prompt_sections import (
    CONTEXT_MANAGEMENT,
    ENVIRONMENT,
    HARNESS,
    IDENTITY,
    PLAN_MODE,
    SESSION_GUIDANCE,
)
from wy_coding_agent.skills import Skill


@dataclass(frozen=True)
class PromptContext:
    """供静态指令分节占位符注入的运行时环境事实。"""

    workspace: Path
    platform: str
    is_git: bool
    date: date
    model: str

    def as_dict(self) -> dict[str, str]:
        """渲染模板用的字符串键值（与 `prompt_sections` 的占位符对齐）。"""
        return {
            "workspace": str(self.workspace),
            "is_git": "yes" if self.is_git else "no",
            "platform": self.platform,
            "date": self.date.isoformat(),
            "model": self.model,
        }


def build_prompt_context(model: str, workspace: Path | None = None) -> PromptContext:
    """收集环境事实：工作目录、git、平台、日期与模型名。"""
    root = (workspace or Path.cwd()).expanduser()
    try:
        is_git = (root / ".git").exists()
    except OSError:
        is_git = False
    return PromptContext(
        workspace=root,
        platform=platform.system(),
        is_git=is_git,
        date=date.today(),
        model=model,
    )


class BasePromptProvider(ABC):
    """Contract for one system prompt section."""

    @abstractmethod
    def provide(self) -> str | None:
        """Return a complete markdown section, or None to omit it.

        Providers must not raise on missing/unreadable sources — a broken
        section skips itself instead of blocking agent startup.
        """


def _read_text(path: Path) -> str | None:
    """Read a UTF-8 text file; missing/unreadable/empty files yield None."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text.strip() or None


class _TemplateProvider(BasePromptProvider):
    """Render one static section template from ``prompt_sections``.

    模板支持 ``{占位符}``（键见 ``PromptContext.as_dict()``）。渲染失败
    （未知占位符或字面花括号未转义）时回落原模板，绝不 raise——一个损坏的
    分节跳过/原样返回，不阻塞 agent 启动。
    """

    _TEMPLATE: str = ""

    def __init__(self, context: PromptContext) -> None:
        self._context = context

    def provide(self) -> str | None:
        try:
            section = self._TEMPLATE.format(**self._context.as_dict())
        except (KeyError, ValueError, IndexError):
            section = self._TEMPLATE
        return section.strip() or None


class IdentityProvider(_TemplateProvider):
    """身份简介（无标题，首行 ``You are ...``）。"""

    _TEMPLATE = IDENTITY


class HarnessProvider(_TemplateProvider):
    """工具/权限/输出行为规范。"""

    _TEMPLATE = HARNESS


class SessionGuidanceProvider(_TemplateProvider):
    """会话内指引（skill 调用、需用户自行执行的 shell）。"""

    _TEMPLATE = SESSION_GUIDANCE


class EnvironmentProvider(_TemplateProvider):
    """运行时环境事实（工作目录/git/平台/日期/模型），占位符注入。"""

    _TEMPLATE = ENVIRONMENT


class ContextManagementProvider(_TemplateProvider):
    """长会话摘要与持久会话说明。"""

    _TEMPLATE = CONTEXT_MANAGEMENT


class PlanModeProvider(BasePromptProvider):
    """plan 模式指令段:harness 状态 plan_active 时经 build_prompt 尾追注入。

    不随默认链装配——由 ``build_prompt(..., harness=...)`` 按 harness 状态
    决定是否追加(见 ``HarnessStateLike``)。
    """

    def provide(self) -> str | None:
        return PLAN_MODE.strip() or None


class TextProvider(BasePromptProvider):
    """Inject one raw markdown section verbatim.

    取代旧的 ``build_prompt(instruction, ...)`` 首参：此前传整段指令字符串
    的调用方，现在传 ``TextProvider("...")``。空/纯空白分节被省略。
    """

    def __init__(self, section: str) -> None:
        self._section = section.strip()

    def provide(self) -> str | None:
        return self._section or None


class AgentMDProvider(BasePromptProvider):
    """Inject project instructions from AGENTS.md, falling back to CLAUDE.md.

    已不在默认链(项目指令移入 ``ClaudeMdReminderProvider`` 每回合注入);
    需走 system prompt 的自定义链可显式加回。
    """

    _CANDIDATES = ("AGENTS.md", "CLAUDE.md")

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or Path.cwd()).expanduser()

    def provide(self) -> str | None:
        for name in self._CANDIDATES:
            content = _read_text(self._root / name)
            if content:
                return f"# Project instructions ({name})\n\n{content}"
        return None


class SkillProvider(BasePromptProvider):
    """List discovered agent skills(L1 元数据,渐进披露的第一级)。

    只注入 name + description；模型经 ``skill`` 工具按需加载正文（L2），
    随包文件用 read/glob/bash 工具取用（L3）。``listed=False`` 的 skill
    不进清单（仍可经工具调用，供用户 ``/name`` 显式触发）。
    """

    def __init__(self, skills: Sequence[Skill]) -> None:
        self._skills = tuple(skill for skill in skills if skill.listed)

    def provide(self) -> str | None:
        if not self._skills:
            return None
        entries = "\n".join(
            f"- {skill.name}: {skill.description}" for skill in self._skills
        )
        return (
            "# Skills\n\n"
            "The following skills are available. When a task matches one, "
            "invoke the `skill` tool with its name to load the full "
            "instructions before proceeding; a user message starting with "
            '"/<name>" is an explicit request to invoke that skill:\n'
            f"{entries}"
        )


class DeferredToolProvider(BasePromptProvider):
    """列出懒加载工具的名字(schema 不随请求发送,故只披露名字)。

    对齐 Claude Code:懒加载工具的完整定义由模型经 ``tool_search`` 工具
    按需取回,清单本身进 system prompt 保持前缀稳定(缓存友好);已加载的
    工具仍留在清单里(重复加载是无害的幂等操作)。
    """

    def __init__(self, tool_names: Sequence[str]) -> None:
        self._names = tuple(name for name in tool_names if name)

    def provide(self) -> str | None:
        if not self._names:
            return None
        entries = "\n".join(f"- {name}" for name in self._names)
        return (
            "# Deferred tools\n\n"
            "The following tools are deferred: only their names are known "
            "here, their schemas are NOT loaded — calling them directly will "
            "fail. Use the `tool_search` tool to load a tool's schema before "
            'calling it (`select:<name>` for a direct pick, or keywords to '
            "search):\n"
            f"{entries}"
        )


class MemoryProvider(BasePromptProvider):
    """Inject persistent memory from a markdown file.

    已不在默认链(自动记忆移入 ``ClaudeMdReminderProvider`` 每回合注入);
    需走 system prompt 的自定义链可显式加回。骨架实现：文件存在且非空时
    全文注入；存放路径约定后续再定。
    """

    def __init__(self, memory_path: Path) -> None:
        self._memory_path = memory_path.expanduser()

    def provide(self) -> str | None:
        content = _read_text(self._memory_path)
        if not content:
            return None
        return f"# Memory\n\n{content}"


def default_instruction_providers(
    context: PromptContext,
) -> tuple[BasePromptProvider, ...]:
    """静态指令段：身份 → Harness → 会话指引 → 环境 → 上下文管理。

    前三段与 Context management 完全静态、Environment 仅注入环境事实；
    整组排在最前且跨会话稳定，利于厂商 prompt 前缀缓存。``# Memory`` /
    ``# Project instructions`` 等动态分节不在此，见 ``default_prompt_providers``。
    """
    return (
        IdentityProvider(context),
        HarnessProvider(context),
        SessionGuidanceProvider(context),
        EnvironmentProvider(context),
        ContextManagementProvider(context),
    )


def default_prompt_providers(
    skills: Sequence[Skill] = (),
    deferred_tools: Sequence[str] = (),
) -> tuple[BasePromptProvider, ...]:
    """Assemble the default dynamic provider chain used by `create_agent`.

    skills 为已发现的 Skill 元组（`wy_coding_agent.skills.discover_skills` 的
    结果，由 factory 传入），缺省为空即不注入 Skills 段；deferred_tools 为懒
    加载工具名（`Tool.deferred` 为真的工具），缺省为空即不注入 Deferred tools
    段。项目指令（AGENTS.md/CLAUDE.md）与 MEMORY.md 已移入
    ``reminders.ClaudeMdReminderProvider``（每回合注入），需要走 system prompt
    的自定义链可显式加回 ``AgentMDProvider``/``MemoryProvider``（本包保留导出）。
    """
    return (
        SkillProvider(skills),
        DeferredToolProvider(deferred_tools),
    )


class HarnessStateLike(Protocol):
    """``build_prompt`` 读取的 harness 状态最小契约。

    满足者（如 ``reminders.HarnessState``）即可驱动条件分节；用 Protocol
    避免 prompt_template 反向 import reminders 造成循环导入。
    """

    plan_active: bool


def build_prompt(
    providers: Sequence[BasePromptProvider] = (),
    *,
    harness: HarnessStateLike | None = None,
) -> str | None:
    """按 harness 状态组装 system prompt;每次提交 LLM 前由 system_builder 调用。

    每个分节先 strip，空/None 分节跳过，以空行连接；``harness.plan_active``
    为真时在链尾追加 ``PlanModeProvider``（plan 模式指令段）。全部为空时返回
    None 供调用方省略该字段。工具 schema 经 API ``tools`` 参数传递，不拼入
    prompt。
    """
    chain = list(providers)
    if harness is not None and harness.plan_active:
        chain.append(PlanModeProvider())
    sections: list[str] = []
    for provider in chain:
        section = provider.provide()
        if section and section.strip():
            sections.append(section.strip())
    if not sections:
        return None
    return "\n\n".join(sections)
