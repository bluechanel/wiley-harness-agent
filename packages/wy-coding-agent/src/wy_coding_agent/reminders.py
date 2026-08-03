"""动态 system-reminder 层:把易变的 harness 状态注入消息流尾部。

与 ``prompt_template.BasePromptProvider``(静态,进 system prompt)对称:
reminder 是每个用户回合被轮询的动态提示,经 ``wy_core.Agent.run`` 的
``reminders`` 参数注入本回合 user 消息尾部的 ``<system-reminder>`` 文本块。
前缀(system prompt/工具/既有历史)保持不变,不破坏厂商的前缀缓存;
过期 reminder 随早期历史被上下文压缩摘要掉,天然具备临时性。
"""

from typing import Protocol

from wy_core import StateExtension


class ReminderProvider(Protocol):
    """每个用户回合被 ConversationService 轮询一次;返回 None 即本回合跳过。"""

    def provide(self) -> str | None: ...


PLAN_MODE_REMINDER = (
    "当前处于 plan 模式:只调研与设计,不要修改任何文件、不要执行有副作用的命令。"
    "先用只读工具充分调研,把实施方案整理为完整的 Markdown 计划后,调用"
    " exit_plan_mode 工具提交(plan 参数为完整方案);调用成功即退出 plan 模式。"
)

PLAN_MODE_EXITED_REMINDER = "plan 模式已退出,可以按既定计划开始实施。"


class PlanModeState(StateExtension):
    """plan 模式的可变状态:激活期每回合注入约束,退出后一次性注入提示。

    每回合重复注入是刻意的:消息流尾部提示的约束力弱于 system prompt,
    持续重申补偿这一点(工具侧硬拦截属后续权限层,当前未实现)。
    同时是 `wy_core.StateExtension`(key="plan_mode"):active 随聚合
    快照进会话 JSONL,恢复会话后模式不丢;一次性的退出提示是易失状态,
    不参与持久化。
    """

    key = "plan_mode"

    def __init__(self) -> None:
        self.active = False
        self._exit_pending = False

    def enable(self) -> None:
        self.active = True
        self._exit_pending = False

    def disable(self) -> None:
        if self.active:
            self.active = False
            self._exit_pending = True

    def provide(self) -> str | None:
        if self.active:
            return PLAN_MODE_REMINDER
        if self._exit_pending:
            self._exit_pending = False
            return PLAN_MODE_EXITED_REMINDER
        return None

    def snapshot(self) -> dict:
        return {"active": self.active}

    def restore(self, data: dict) -> None:
        self.active = bool(data.get("active", False))
        self._exit_pending = False
