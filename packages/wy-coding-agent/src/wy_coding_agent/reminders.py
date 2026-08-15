"""动态 system-reminder 层与 harness 全局状态。

- ``ClaudeMdReminderProvider``：每个用户回合被轮询一次，把 claudeMd 上下文
  （全局/项目 CLAUDE.md + 自动记忆 + currentDate）渲染为 ``<system-reminder>``
  文本块，经 ``wy_core.Agent.run`` 的 ``reminders`` 参数注入本回合 user 消息
  尾部。前缀（system prompt/工具/既有历史）保持不变，不破坏厂商前缀缓存。
- ``HarnessState``：harness 全局可变状态（现仅 plan 模式一个字段），TUI
  ``/plan`` 与 ``exit_plan_mode`` 工具都只翻转它；plan 约束经
  ``prompt_template.build_prompt(..., harness=...)`` 在 system prompt 层按
  状态组装，不再走本层。
"""

from datetime import date
from pathlib import Path
from typing import Protocol

from wy_core import StateExtension

from wy_coding_agent.prompt_template import _read_text


class ReminderProvider(Protocol):
    """每个用户回合被 ConversationService 轮询一次;返回 None 即本回合跳过。"""

    def provide(self) -> str | None: ...


class HarnessState(StateExtension):
    """harness 全局可变状态;当前仅承载 plan 模式。

    key 沿用 "plan_mode" 以兼容既有会话快照（``AgentState.restore`` 按 key
    分发）。不提供 provide()——plan 约束由 ``build_prompt(..., harness=...)``
    在 system prompt 层按状态组装。快照格式 ``{"plan_active": bool}``，
    restore 兼容旧 ``{"active": bool}``。
    """

    key = "plan_mode"

    def __init__(self) -> None:
        self.plan_active = False

    def enable_plan(self) -> None:
        self.plan_active = True

    def disable_plan(self) -> None:
        self.plan_active = False

    def snapshot(self) -> dict:
        return {"plan_active": self.plan_active}

    def restore(self, data: dict) -> None:
        self.plan_active = bool(data.get("plan_active", data.get("active", False)))


class ClaudeMdReminderProvider:
    """claudeMd 上下文注入:每回合把全局/项目指令与 memory 注入 user 尾部。

    满足 ``ReminderProvider`` 协议(provide() -> str | None)。读取(缺失/不可读
    跳过对应分节,绝不 raise):
    - 全局 ``~/.claude/CLAUDE.md``
    - 项目指令:``workspace/AGENTS.md`` 优先,回落 ``workspace/CLAUDE.md``
    - 自动记忆:``workspace/MEMORY.md``
    恒有 ``# currentDate`` + IMPORTANT 尾部;全缺时仅输出该尾部。
    """

    _GLOBAL_LABEL = "user's private global instructions for all projects"
    _PROJECT_LABEL = "project instructions, checked into the codebase"
    _MEMORY_LABEL = "user's auto-memory, persists across conversations"

    def __init__(
        self,
        *,
        workspace: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self._workspace = (workspace or Path.cwd()).expanduser()
        self._home = (home or Path.home()).expanduser()

    def _file_section(self, path: Path, label: str) -> str | None:
        content = _read_text(path)
        if content is None:
            return None
        return f"Contents of {path} ({label}):\n{content}"

    def provide(self) -> str | None:
        parts: list[str] = []
        section = self._file_section(self._home / ".claude" / "CLAUDE.md", self._GLOBAL_LABEL)
        if section:
            parts.append(section)
        section = self._file_section(self._workspace / "AGENTS.md", self._PROJECT_LABEL)
        if section:
            parts.append(section)
        else:
            section = self._file_section(self._workspace / "CLAUDE.md", self._PROJECT_LABEL)
            if section:
                parts.append(section)
        section = self._file_section(self._workspace / "MEMORY.md", self._MEMORY_LABEL)
        if section:
            parts.append(section)

        tail = (
            f"# currentDate\n{date.today().isoformat()}\n\n"
            "IMPORTANT: this background context may or may not be relevant to the "
            "current task, but you should act as if it is part of your instructions."
        )
        if not parts:
            return tail
        return "# claudeMd\n\n" + "\n\n".join(parts) + "\n\n" + tail
