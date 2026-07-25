"""Composable prompt providers that assemble the system prompt.

每个 `BasePromptProvider` 子类贡献 system prompt 的一个段落；
`build_prompt` 负责把 instruction 与各 provider 段落组合成最终提示词。
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from pathlib import Path
import platform

from wiley_agent.config import AnthropicConfig


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


class ModelProvider(BasePromptProvider):
    """Declare the model that powers the agent."""

    def __init__(self, model: str) -> None:
        self._model = model.strip()

    def provide(self) -> str | None:
        if not self._model:
            return None
        return f"# Model\n\nYou are powered by the model `{self._model}`."


class WorkspaceProvider(BasePromptProvider):
    """Describe the working environment: cwd, platform, git, date."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or Path.cwd()).expanduser()

    def provide(self) -> str | None:
        try:
            is_git_repo = (self._root / ".git").exists()
        except OSError:
            is_git_repo = False
        return "\n".join(
            (
                "# Workspace",
                "",
                f"- Working directory: {self._root}",
                f"- Is a git repository: {'yes' if is_git_repo else 'no'}",
                f"- Platform: {platform.system()}",
                f"- Today's date: {date.today().isoformat()}",
            )
        )


class AgentMDProvider(BasePromptProvider):
    """Inject project instructions from AGENTS.md, falling back to CLAUDE.md."""

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
    """List skill files for on-demand reading.

    骨架实现：扫描目录下的 ``*.md`` 并列出名称与路径，模型按需用文件工具
    读取全文；注入格式后续再迭代。
    """

    def __init__(self, skills_dir: Path) -> None:
        self._skills_dir = skills_dir.expanduser()

    def provide(self) -> str | None:
        try:
            skill_files = sorted(
                path for path in self._skills_dir.glob("*.md") if path.is_file()
            )
        except OSError:
            return None
        if not skill_files:
            return None
        entries = "\n".join(f"- {path.stem}: {path}" for path in skill_files)
        return (
            "# Skills\n\n"
            "The following skill files are available. When a task matches one, "
            "read its full content with your file tools before proceeding:\n"
            f"{entries}"
        )


class MemoryProvider(BasePromptProvider):
    """Inject persistent memory from a markdown file.

    骨架实现：文件存在且非空时全文注入；存放路径约定后续再定。
    """

    def __init__(self, memory_path: Path) -> None:
        self._memory_path = memory_path.expanduser()

    def provide(self) -> str | None:
        content = _read_text(self._memory_path)
        if not content:
            return None
        return f"# Memory\n\n{content}"


def default_prompt_providers(
    config: AnthropicConfig,
    workspace: Path | None = None,
) -> tuple[BasePromptProvider, ...]:
    """Assemble the default provider chain used by `create_agent`."""
    root = (workspace or Path.cwd()).expanduser()
    return (
        ModelProvider(config.model),
        WorkspaceProvider(root),
        AgentMDProvider(root),
        SkillProvider(root / "skills"),
        MemoryProvider(root / "MEMORY.md"),
    )


def build_prompt(
    instruction: str | None = None,
    providers: Sequence[BasePromptProvider] = (),
) -> str | None:
    """Compose the system prompt: instruction first, then provider sections.

    Empty sections are skipped; returns None when nothing contributes so
    callers can omit the field. Tool schemas are sent via the API ``tools``
    parameter, not the prompt.
    """
    sections: list[str] = []
    if instruction and instruction.strip():
        sections.append(instruction.strip())
    for provider in providers:
        section = provider.provide()
        if section and section.strip():
            sections.append(section.strip())
    if not sections:
        return None
    return "\n\n".join(sections)
