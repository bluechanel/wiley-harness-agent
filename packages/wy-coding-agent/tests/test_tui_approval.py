"""工具审批冒烟测试：入参预览/选项文案的纯函数断言 + 审批链路交互。

通用选择组件本身的键盘交互在 ``test_tui_choice.py``；
这里只验证"工具调用 → 卡片文案 → 裁决"这一层的映射与桥接。
"""

import asyncio
import time
from pathlib import Path

from textual.widgets import Input, Static

from wy_core import ToolApproval, ToolCall, ToolResult

from wy_coding_agent.tool_policy import ApprovalHandler, WorkspaceToolHook
from wy_coding_agent.tui.approval import (
    TuiApprovalHandler,
    build_choices,
    heading,
    preview_text,
    question,
)
from wy_coding_agent.tui.app import ChatApp
from wy_coding_agent.tui.choice import ChoiceWidget

_TIMEOUT_SECONDS = 5.0


class FakeBackend:
    """审批测试用的最简 ChatBackend 桩。"""

    def __init__(self, tool_hook=None):
        self.tool_hook = tool_hook
        self.plan_mode = None

    async def stream(self, user_input: str):
        if False:
            yield
        return

    def save_state(self) -> None:
        pass


class RememberingHook:
    """支持"不再询问"的最简 hook 桩（鸭子类型，仅供 handler 探测）。"""

    def __init__(self, can: bool = True):
        self._can = can
        self.remembered: list[ToolCall] = []

    def can_remember(self, call: ToolCall) -> bool:
        return self._can

    def allow_always(self, call: ToolCall) -> None:
        self.remembered.append(call)


async def _wait_until(pilot, condition) -> None:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("等待界面状态超时")
        await pilot.pause(0.01)


# ── 卡片文案 ────────────────────────────────────────────────


def test_heading_and_question_per_tool() -> None:
    edit = ToolCall(id="c1", name="edit", input={})
    assert heading(edit) == "编辑文件"
    assert question(edit) == "是否应用该编辑？"


def test_heading_and_question_fall_back_for_unknown_tool() -> None:
    grep = ToolCall(id="c1", name="grep", input={})
    assert heading(grep) == "grep 工具调用"
    assert question(grep) == "是否继续？"


def test_preview_bash_shows_command() -> None:
    text = preview_text(ToolCall(id="c1", name="bash", input={"command": "ls -la"}))
    assert text.plain == "  ls -la"


def test_preview_bash_appends_description() -> None:
    text = preview_text(
        ToolCall(
            id="c1",
            name="bash",
            input={"command": "ls", "description": "列目录"},
        )
    )
    assert text.plain.splitlines() == ["  ls", "  列目录"]


def test_preview_read_shows_path() -> None:
    text = preview_text(
        ToolCall(id="c1", name="read", input={"file_path": "/tmp/f.py"})
    )
    assert text.plain == "  /tmp/f.py"


def test_preview_edit_renders_diff() -> None:
    text = preview_text(
        ToolCall(
            id="c1",
            name="edit",
            input={
                "file_path": "/tmp/a.py",
                "old_string": "hello",
                "new_string": "world",
            },
        )
    )
    assert text.plain.splitlines() == ["  /tmp/a.py", "", "  - hello", "  + world"]


def test_preview_write_clips_long_content() -> None:
    text = preview_text(
        ToolCall(
            id="c1",
            name="write",
            input={"file_path": "/tmp/f.py", "content": "\n".join("x" * 40)},
        )
    )
    assert "另有" in text.plain  # 超过 12 行折叠成一行提示


def test_preview_unknown_tool_falls_back_to_json() -> None:
    text = preview_text(ToolCall(id="c1", name="grep", input={"pattern": "foo"}))
    assert "foo" in text.plain


def test_preview_no_input() -> None:
    text = preview_text(ToolCall(id="c1", name="grep", input={}))
    assert "无参数" in text.plain


# ── 选项构造 ────────────────────────────────────────────────


def test_choices_without_remember() -> None:
    choices = build_choices(
        ToolCall(id="c1", name="bash", input={"command": "ls"}), can_remember=False
    )
    assert [c.value.allowed for c in choices] == [True, False]


def test_choices_with_remember() -> None:
    choices = build_choices(
        ToolCall(id="c1", name="bash", input={"command": "ls"}), can_remember=True
    )
    assert [c.value.allowed for c in choices] == [True, True, False]
    assert choices[1].value.remember is True
    assert "该命令" in choices[1].label


# ── handler 桥接 ──────────────────────────────────────────


def test_handler_offers_remember_when_hook_supports_it() -> None:
    """hook 提供 can_remember/allow_always 时，卡片出现三个选项。"""
    hook = RememberingHook()

    async def go():
        app = ChatApp(FakeBackend(hook), session_id="s")
        async with app.run_test() as pilot:
            handler = TuiApprovalHandler(app)
            task = asyncio.create_task(
                handler.request_approval(
                    ToolCall(id="c1", name="bash", input={"command": "ls"})
                )
            )
            await _wait_until(pilot, lambda: bool(app.query(ChoiceWidget)))
            widget = app.query_one(ChoiceWidget)
            count = len(widget.query(".choice-option"))
            await pilot.press("2")
            return count, await asyncio.wait_for(task, timeout=_TIMEOUT_SECONDS)

    count, result = asyncio.run(go())
    assert count == 3
    assert result.allowed is True
    assert len(hook.remembered) == 1  # 选中"不再询问"回调了 hook


def test_handler_hides_remember_when_hook_lacks_support() -> None:
    """hook 无记忆能力时只有两个选项。"""

    async def go():
        app = ChatApp(FakeBackend(object()), session_id="s")
        async with app.run_test() as pilot:
            handler = TuiApprovalHandler(app)
            task = asyncio.create_task(
                handler.request_approval(
                    ToolCall(id="c1", name="bash", input={"command": "ls"})
                )
            )
            await _wait_until(pilot, lambda: bool(app.query(ChoiceWidget)))
            count = len(app.query_one(ChoiceWidget).query(".choice-option"))
            await pilot.press("escape")
            return count, await asyncio.wait_for(task, timeout=_TIMEOUT_SECONDS)

    count, result = asyncio.run(go())
    assert count == 2
    assert result.allowed is False


def test_handler_renders_tool_heading_and_preview() -> None:
    """卡片文案确实由工具调用推出来。"""

    async def go():
        app = ChatApp(FakeBackend(), session_id="s")
        async with app.run_test() as pilot:
            handler = TuiApprovalHandler(app)
            task = asyncio.create_task(
                handler.request_approval(
                    ToolCall(id="c1", name="bash", input={"command": "ls -la"})
                )
            )
            await _wait_until(pilot, lambda: bool(app.query(ChoiceWidget)))
            widget = app.query_one(ChoiceWidget)
            head = str(widget.query_one("#choice-heading", Static).content)
            body = widget.query_one("#choice-body", Static).content.plain
            await pilot.press("escape")
            await asyncio.wait_for(task, timeout=_TIMEOUT_SECONDS)
            return head, body

    head, body = asyncio.run(go())
    assert head == "Bash 命令"
    assert body == "  ls -la"


def test_tui_approval_handler_stub() -> None:
    """TuiApprovalHandler 的审批 handler 协议正确实现。"""

    class StubHandler(ApprovalHandler):
        async def request_approval(self, call: ToolCall) -> ToolApproval:
            return ToolApproval(allowed=True, reason="桩通过")

    handler = StubHandler()

    async def go():
        return await handler.request_approval(
            ToolCall(id="c3", name="bash", input={"cmd": "ls"})
        )

    result = asyncio.run(go())
    assert result.allowed is True


# ── 真实时序：回合进行中卡片必须可交互 ─────────────────────


class ApprovalBackend:
    """回合进行到一半调 hook.approve —— 复刻真实审批时序的后端桩。

    真实链路里 ``ConversationService.stream`` 在 Agent 循环内触发审批，
    审批 await 期间回合尚未结束；本桩把这一时序压缩成两条事件。
    """

    def __init__(self):
        self.hook = WorkspaceToolHook(Path("/nonexistent-workspace"))
        self.tool_hook = self.hook
        self.plan_mode = None
        self.decision: ToolApproval | None = None

    async def stream(self, user_input: str):
        call = ToolCall(id="t1", name="bash", input={"command": "ls"})
        yield call
        self.decision = await self.hook.approve(call)
        yield ToolResult(id="t1", name="bash", content="ok", is_error=False)

    def save_state(self) -> None:
        pass


def test_approval_card_responds_to_keys_during_turn() -> None:
    """回归：回合进行中挂出的审批卡片必须能接收键盘事件。

    turn 循环若直接 await 在 App 的消息处理器里，会堵死消息泵——
    卡片挂得出来但收不到任何按键，界面看着像卡死。
    """

    async def go():
        backend = ApprovalBackend()
        app = ChatApp(backend, session_id="s")
        async with app.run_test() as pilot:
            await _wait_until(pilot, lambda: bool(app.query(".banner")))
            app.query_one("#prompt", Input).value = "跑一下"
            await pilot.press("enter")
            await _wait_until(pilot, lambda: bool(app.query(ChoiceWidget)))
            await pilot.press("enter")  # 选中第一项：批准
            await _wait_until(pilot, lambda: backend.decision is not None)
            return backend.decision

    result = asyncio.run(asyncio.wait_for(go(), timeout=_TIMEOUT_SECONDS * 3))
    assert result is not None and result.allowed is True


def test_approval_card_arrow_keys_during_turn() -> None:
    """回归：回合进行中上下键也要能移动光标（另一半卡死症状）。"""

    async def go():
        backend = ApprovalBackend()
        app = ChatApp(backend, session_id="s")
        async with app.run_test() as pilot:
            await _wait_until(pilot, lambda: bool(app.query(".banner")))
            app.query_one("#prompt", Input).value = "跑一下"
            await pilot.press("enter")
            await _wait_until(pilot, lambda: bool(app.query(ChoiceWidget)))
            widget = app.query_one(ChoiceWidget)
            await pilot.press("down")
            await pilot.press("down")  # hook 支持"不再询问"，末项是第三项
            moved = widget.query_one("#choice-option-2", Static).content.plain
            await pilot.press("enter")  # 此时光标在末项：拒绝
            await _wait_until(pilot, lambda: backend.decision is not None)
            return moved, backend.decision

    moved, result = asyncio.run(asyncio.wait_for(go(), timeout=_TIMEOUT_SECONDS * 3))
    assert moved.startswith("❯ 3.")
    assert result is not None and result.allowed is False
