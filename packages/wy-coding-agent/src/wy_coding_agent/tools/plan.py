"""plan 模式退出工具:模型完成方案后经此提交计划并退出 plan 模式。

与 ``skill``/``mcp_tool`` 同型:模块只有类、无模块级实例,自动扫描不收录;
由 factory 构造并持有与 ConversationService 共享的 ``HarnessState``。
v1 调用即退出,无用户审批对话框;审批交互与 plan 模式下的工具硬拦截
随后续权限层补充。本工具只翻转 harness 状态(``disable_plan``)与提交计划;
plan 约束经 ``prompt_template.build_prompt(..., harness=...)`` 在 system
prompt 层按状态组装,``exit_plan_mode`` 翻转后下一次提交的 system 即无
plan 段。
"""

from wy_core import Tool

from wy_coding_agent.reminders import HarnessState


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

    def __init__(self, state: HarnessState) -> None:
        self._state = state

    def execute(self, input: dict) -> str:
        if not str(input.get("plan", "")).strip():
            raise RuntimeError("plan 不能为空:请提交完整的实施方案")
        if not self._state.plan_active:
            return "当前不在 plan 模式,无需退出;请继续当前任务。"
        self._state.disable_plan()
        return "计划已提交,plan 模式已退出,可以开始实施。"
