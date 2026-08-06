"""工具审批策略 Hook：基于工作区的工具调用审批。

``WorkspaceToolHook`` 实现 ``wy_core.ToolHook``，策略：
- bash：一律需要用户审批
- read/write/edit：工作区内文件直接放行，工作区外需审批
- 其余工具：默认放行

``ApprovalHandler`` 是用户审批交互的抽象——TUI 弹窗、CLI 询问、
测试桩各自实现。Hook 不含 UI 依赖；handler 通过 ``set_handler()``
延迟注入。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from wy_core import ToolApproval, ToolCall, ToolHook

from wy_coding_agent.tools.files import FileToolError, resolve_path


class ApprovalHandler(ABC):
    """用户审批交互抽象。TUI、CLI、测试桩各自实现。"""

    @abstractmethod
    async def request_approval(self, call: ToolCall) -> ToolApproval:
        """展示审批 UI 并等待用户决定。"""


class WorkspaceToolHook(ToolHook):
    """基于工作区的工具审批 Hook。

    策略常量（可被子类覆写）：
    - ``_ALWAYS_APPROVE``：命中工具名 → 需要用户审批
    - ``_PATH_GATED``：命中工具名 → 按路径是否在工作区内决定
    - ``_PATH_KEY``：路径从 call.input 的哪个键提取

    构造时注入 workspace；handler 可延迟注入（TUI 创建晚于 Agent）。
    handler 缺失时，需要审批的工具会被拒绝——无头模式自动降级。
    """

    _ALWAYS_APPROVE: frozenset[str] = frozenset({"bash"})
    _PATH_GATED: frozenset[str] = frozenset({"read", "write", "edit"})
    _PATH_KEY: str = "file_path"

    def __init__(
        self,
        workspace: Path,
        handler: ApprovalHandler | None = None,
    ) -> None:
        self._workspace = workspace.resolve()
        self._handler = handler

    def set_handler(self, handler: ApprovalHandler | None) -> None:
        """注入审批交互实现（TUI 在 on_mount 时调用）。"""
        self._handler = handler

    # ── ToolHook 接口 ──────────────────────────────────────

    async def approve(self, call: ToolCall) -> ToolApproval:
        decision = self._evaluate_policy(call)
        if decision is not None:
            return decision
        # 策略返回 None 表示"需要审批"
        if self._handler is not None:
            return await self._handler.request_approval(call)
        return ToolApproval(
            allowed=False,
            reason=f"工具 '{call.name}' 需要用户审批（当前无可用的审批交互）",
        )

    # ── 策略判断 ───────────────────────────────────────────

    def _evaluate_policy(self, call: ToolCall) -> ToolApproval | None:
        """返回 ToolApproval 表示直接裁决；返回 None 表示需要用户审批。"""
        # 1. 一律需审批的工具
        if call.name in self._ALWAYS_APPROVE:
            return None

        # 2. 按路径决定的工具
        if call.name in self._PATH_GATED:
            path = self._resolve_tool_path(call)
            if path is not None and path.is_relative_to(self._workspace):
                return ToolApproval(allowed=True)
            # 路径不存在、无效或在工作区外 → 需要审批
            return None

        # 3. 其余工具默认放行
        return ToolApproval(allowed=True)

    # ── 路径提取 ───────────────────────────────────────────

    def _resolve_tool_path(self, call: ToolCall) -> Path | None:
        """从 call.input 中提取路径并规范化为绝对路径。

        复用 ``resolve_path``（与文件工具自身的路径规范化一致）；
        提取失败返回 None。
        """
        raw = call.input.get(self._PATH_KEY)
        if not raw:
            return None
        try:
            return resolve_path(raw).resolve()
        except FileToolError:
            return None
