"""plan 模式退出工具:模型完成方案后经此提交计划并退出 plan 模式。

与 ``skill``/``mcp_tool`` 同型:模块只有类、无模块级实例,自动扫描不收录;
由 factory 构造并持有与 ConversationService 共享的 ``PlanModeState``。
v1 调用即退出,无用户审批对话框;审批交互与 plan 模式下的工具硬拦截
随后续权限层补充。plan 模式的约束提示由 ``reminders.PlanModeState``
逐回合注入,本工具只负责状态翻转与计划提交。
"""

from wy_core import Tool

from wy_coding_agent.reminders import PlanModeState


class ExitPlanModeTool(Tool):
    """Submit the finished implementation plan and leave plan mode."""

    name = "exit_plan_mode"
    description = (
        "Call this tool ONLY while in plan mode, after your research is done "
        "and the implementation plan is complete. Pass the full plan in "
        "Markdown; a successful call exits plan mode so implementation can "
        "start. Never call it outside plan mode."
    )
    parameters = {
        "type": "object",
        "properties": {
            "plan": {
                "type": "string",
                "description": "The complete implementation plan in Markdown.",
            }
        },
        "required": ["plan"],
        "additionalProperties": False,
    }

    def __init__(self, state: PlanModeState) -> None:
        self._state = state

    def execute(self, input: dict) -> str:
        if not str(input.get("plan", "")).strip():
            raise RuntimeError("plan 不能为空:请提交完整的实施方案")
        if not self._state.active:
            return "当前不在 plan 模式,无需退出;请继续当前任务。"
        self._state.disable()
        return "计划已提交,plan 模式已退出,可以开始实施。"
