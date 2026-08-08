"""工具审批策略 Hook：基于工作区的工具调用审批。

``WorkspaceToolHook`` 实现 ``wy_core.ToolHook``，策略：
- 将审批决策委托给各工具的 ``Tool.approve()`` 方法
- ``approve()`` 返回 None → 直接放行
- ``approve()`` 返回 ApprovalRequest → 进入用户审批流程

``ApprovalHandler`` 是用户审批交互的抽象——TUI 弹窗、CLI 询问、
测试桩各自实现。Hook 不含 UI 依赖；handler 通过 ``set_handler()``
延迟注入。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from wy_core import ApprovalRequest, Tool, ToolApproval, ToolCall, ToolHook


class ApprovalHandler(ABC):
    """用户审批交互抽象。TUI、CLI、测试桩各自实现。"""

    @abstractmethod
    async def request_approval(
        self, call: ToolCall, request: ApprovalRequest
    ) -> ToolApproval:
        """展示审批 UI 并等待用户决定。"""


class WorkspaceToolHook(ToolHook):
    """基于工作区的工具审批 Hook。

    审批策略由各工具自身的 ``approve()`` 方法决定；本 Hook 仅负责编排：
    调用工具的 ``approve()``、检查记忆缓存、委托 handler 展示 UI。

    构造时注入 workspace 与 tools 映射；handler 可延迟注入
    （TUI 创建晚于 Agent）。handler 缺失时，需要审批的工具会被拒绝——
    无头模式自动降级。用户选"不再询问"时经 ``allow_always`` 记住
    该调用（内存态、随进程消失），命中记忆的调用不再打扰 handler。
    """

    def __init__(
        self,
        workspace: Path,
        tools: dict[str, Tool],
        handler: ApprovalHandler | None = None,
    ) -> None:
        self._workspace = workspace.resolve()
        self._tools = tools
        self._handler = handler
        self._remembered: set[str] = set()

    def set_handler(self, handler: ApprovalHandler | None) -> None:
        """注入审批交互实现（TUI 在 on_mount 时调用）。"""
        self._handler = handler

    # ── 记住选择（会话内存态，不持久化） ───────────────────

    def can_remember(self, call: ToolCall) -> bool:
        """该调用是否可被"不再询问"记住。"""
        tool = self._tools.get(call.name)
        if tool is None:
            return False
        try:
            request = tool.approve(call.input, self._workspace)
        except Exception:
            return False
        return request is not None and request.key is not None

    def allow_always(self, call: ToolCall) -> None:
        """记住这次批准：同 key 的后续调用本进程内直接放行。"""
        tool = self._tools.get(call.name)
        if tool is None:
            return
        try:
            request = tool.approve(call.input, self._workspace)
        except Exception:
            return
        if request is not None and request.key is not None:
            self._remembered.add(request.key)

    # ── ToolHook 接口 ──────────────────────────────────────

    async def approve(self, call: ToolCall) -> ToolApproval:
        """审批一次工具调用。将决策委托给工具自身的 approve() 方法。"""
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolApproval(
                allowed=False, reason=f"未知工具: {call.name}"
            )

        try:
            request = tool.approve(call.input, self._workspace)
        except Exception as exc:
            return ToolApproval(allowed=False, reason=str(exc))

        if request is None:
            return ToolApproval(allowed=True)

        if request.key is not None and request.key in self._remembered:
            return ToolApproval(allowed=True, reason="本次会话已批准")

        if self._handler is not None:
            return await self._handler.request_approval(call, request)

        return ToolApproval(
            allowed=False,
            reason=f"工具 '{call.name}' 需要用户审批（当前无可用的审批交互）",
        )
