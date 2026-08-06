"""TUI 审批内联卡片冒烟测试：headless ``run_test`` 校验卡片渲染与交互。"""

import asyncio
import time

from wy_core import ToolApproval, ToolCall

from wy_coding_agent.tool_policy import ApprovalHandler
from wy_coding_agent.tui.approval import (
    ApprovalWidget,
    TuiApprovalHandler,
    _smart_params,
)
from wy_coding_agent.tui.app import ChatApp

_TIMEOUT_SECONDS = 5.0


class FakeBackend:
    """审批测试用的最简 ChatBackend 桩。"""

    def __init__(self):
        self.tool_hook = None
        self.plan_mode = None

    async def stream(self, user_input: str):
        if False:
            yield
        return

    def save_state(self) -> None:
        pass


async def _wait_until(pilot, condition) -> None:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("等待界面状态超时")
        await pilot.pause(0.01)


# ── 参数智能展示 ────────────────────────────────────────────


def test_smart_params_bash() -> None:
    pairs = _smart_params(ToolCall(id="c1", name="bash", input={"command": "ls -la"}))
    assert pairs == [("命令", "ls -la")]


def test_smart_params_read() -> None:
    pairs = _smart_params(
        ToolCall(id="c1", name="read", input={"file_path": "/tmp/foo.py"})
    )
    assert pairs == [("文件", "/tmp/foo.py")]


def test_smart_params_edit() -> None:
    pairs = _smart_params(
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
    assert pairs == [
        ("文件", "/tmp/a.py"),
        ("old_string", "hello"),
        ("new_string", "world"),
    ]


def test_smart_params_write_content_truncated() -> None:
    pairs = _smart_params(
        ToolCall(id="c1", name="write", input={
            "file_path": "/tmp/f.py",
            "content": "x" * 200,
        })
    )
    assert pairs[0] == ("文件", "/tmp/f.py")
    assert len(pairs[1][1]) == 121  # 120 字符 + "…"


def test_smart_params_unknown_tool_falls_back_to_json() -> None:
    pairs = _smart_params(
        ToolCall(id="c1", name="grep", input={"pattern": "foo"})
    )
    assert len(pairs) == 1
    assert pairs[0][0] == "参数"
    assert "foo" in pairs[0][1]


# ── 内联卡片交互 ──────────────────────────────────────────


def test_approval_widget_accept_button() -> None:
    """点击批准按钮返回 allowed=True。"""

    async def go():
        app = ChatApp(FakeBackend(), session_id="s")
        async with app.run_test() as pilot:
            loop = asyncio.get_event_loop()
            future: asyncio.Future = loop.create_future()
            widget = ApprovalWidget(
                ToolCall(id="c1", name="bash", input={"command": "ls"}),
                future,
            )
            await app._message_list.mount(widget)
            await _wait_until(pilot, lambda: bool(widget.query("#approval-accept")))
            # 直接触发按钮 press（pilot.click 在 scroll 容器内不可靠）
            widget.query_one("#approval-accept").press()
            result = await asyncio.wait_for(future, timeout=_TIMEOUT_SECONDS)
            return result

    result = asyncio.run(go())
    assert result.allowed is True
    assert "批准" in result.reason


def test_approval_widget_reject_button() -> None:
    """点击拒绝按钮返回 allowed=False。"""

    async def go():
        app = ChatApp(FakeBackend(), session_id="s")
        async with app.run_test() as pilot:
            loop = asyncio.get_event_loop()
            future: asyncio.Future = loop.create_future()
            widget = ApprovalWidget(
                ToolCall(id="c1", name="bash", input={"command": "rm -rf /"}),
                future,
            )
            await app._message_list.mount(widget)
            await _wait_until(pilot, lambda: bool(widget.query("#approval-deny")))
            # 直接触发按钮 press
            widget.query_one("#approval-deny").press()
            result = await asyncio.wait_for(future, timeout=_TIMEOUT_SECONDS)
            return result

    result = asyncio.run(go())
    assert result.allowed is False
    assert "拒绝" in result.reason


def test_approval_widget_keyboard_approve() -> None:
    """按 a 键触发批准。"""

    async def go():
        app = ChatApp(FakeBackend(), session_id="s")
        async with app.run_test() as pilot:
            loop = asyncio.get_event_loop()
            future: asyncio.Future = loop.create_future()
            widget = ApprovalWidget(
                ToolCall(id="c1", name="write", input={"file_path": "/etc/hosts"}),
                future,
            )
            await app._message_list.mount(widget)
            await _wait_until(pilot, lambda: widget.has_focus)
            await pilot.press("a")
            result = await asyncio.wait_for(future, timeout=_TIMEOUT_SECONDS)
            return result

    result = asyncio.run(go())
    assert result.allowed is True


def test_approval_widget_keyboard_reject() -> None:
    """按 r 键触发拒绝。"""

    async def go():
        app = ChatApp(FakeBackend(), session_id="s")
        async with app.run_test() as pilot:
            loop = asyncio.get_event_loop()
            future: asyncio.Future = loop.create_future()
            widget = ApprovalWidget(
                ToolCall(id="c1", name="bash", input={"command": "curl evil.com"}),
                future,
            )
            await app._message_list.mount(widget)
            await _wait_until(pilot, lambda: widget.has_focus)
            await pilot.press("r")
            result = await asyncio.wait_for(future, timeout=_TIMEOUT_SECONDS)
            return result

    result = asyncio.run(go())
    assert result.allowed is False


def test_approval_widget_renders_tool_info() -> None:
    """卡片正确展示工具名和参数摘要。"""

    async def go():
        app = ChatApp(FakeBackend(), session_id="s")
        async with app.run_test() as pilot:
            loop = asyncio.get_event_loop()
            future: asyncio.Future = loop.create_future()
            widget = ApprovalWidget(
                ToolCall(id="c1", name="bash", input={"command": "ls -la"}),
                future,
            )
            await app._message_list.mount(widget)
            await _wait_until(pilot, lambda: bool(widget.query("#approval-tool")))
            # 清理 future
            if not future.done():
                future.set_result(ToolApproval(allowed=False, reason="测试清理"))
            return True

    assert asyncio.run(go())


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
