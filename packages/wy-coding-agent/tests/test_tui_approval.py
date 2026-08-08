"""工具审批冒烟测试：ApprovalRequest 渲染/选项文案的纯函数断言 + 审批链路交互。

通用选择组件本身的键盘交互在 ``test_tui_choice.py``；
这里只验证"ApprovalRequest → 卡片文案 → 裁决"这一层的映射与桥接。
"""

import asyncio
import time
from pathlib import Path

from textual.widgets import Input, Static

from wy_core import ApprovalRequest, ToolApproval, ToolCall, ToolResult

from wy_coding_agent.tool_policy import ApprovalHandler, WorkspaceToolHook
from wy_coding_agent.tui.approval import (
    TuiApprovalHandler,
    build_choices,
    render_fields,
)
from wy_coding_agent.tui.app import ChatApp
from wy_coding_agent.tui.choice import ChoiceWidget
from wy_coding_agent.tools.bash import BashTool
from wy_coding_agent.tools.edit import EditTool
from wy_coding_agent.tools.read import ReadTool
from wy_coding_agent.tools.write import WriteTool

_TIMEOUT_SECONDS = 5.0

_BASH = BashTool()
_READ = ReadTool()
_WRITE = WriteTool()
_EDIT = EditTool()
_STANDARD_TOOLS = {"bash": _BASH, "read": _READ, "write": _WRITE, "edit": _EDIT}


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


# ── ApprovalRequest 检验 ─────────────────────────────────────


def test_bash_approval_request() -> None:
    """Bash 工具的 approve() 返回正确的审批请求。"""
    req = _BASH.approve({"command": "ls -la"}, Path("/tmp"))
    assert req is not None
    assert req.heading == "Bash 命令"
    assert req.question == "是否执行该命令？"
    assert ("命令", "ls -la") in req.fields
    assert req.key == "bash:ls -la"


def test_read_approval_request_outside_workspace() -> None:
    """Read 工具对工作区外文件返回审批请求。"""
    req = _READ.approve({"file_path": "/etc/passwd"}, Path("/nonexistent"))
    assert req is not None
    assert req.heading == "读取文件"
    assert req.question == "是否读取该文件？"


def test_read_approve_in_workspace_allows() -> None:
    """Read 工具对工作区内文件返回 None（放行）。"""
    import tempfile
    ws = Path(tempfile.mkdtemp()).resolve()
    f = ws / "test.py"
    f.write_text("hello")
    req = _READ.approve({"file_path": str(f)}, ws)
    assert req is None


# ── 卡片渲染 ────────────────────────────────────────────────


def test_render_fields_single_field() -> None:
    text = render_fields([("命令", "ls -la")])
    assert "ls -la" in text.plain


def test_render_fields_multiple_fields() -> None:
    text = render_fields([("命令", "ls"), ("说明", "列目录")])
    lines = text.plain.splitlines()
    assert "ls" in lines[0]
    assert "列目录" in lines[1]


def test_render_fields_diff_coloring() -> None:
    """删除标签用红色，新增标签用绿色。"""
    text = render_fields([("文件", "/tmp/a.py"), ("删除", "hello"), ("新增", "world")])
    lines = text.plain.splitlines()
    assert "/tmp/a.py" in lines[0]
    assert "hello" in lines[1]
    assert "world" in lines[2]


def test_render_fields_long_content_clipped() -> None:
    text = render_fields([("内容", "\n".join("x" * 40))])
    assert "另有" in text.plain  # 超过 12 行折叠成一行提示


# ── 选项构造 ────────────────────────────────────────────────


def test_choices_without_remember_key() -> None:
    req = ApprovalRequest(heading="测试", question="是否？", key=None)
    choices = build_choices(req)
    assert [c.value.allowed for c in choices] == [True, False]


def test_choices_with_remember_key() -> None:
    req = ApprovalRequest(heading="测试", question="是否？", key="test:key")
    choices = build_choices(req)
    assert [c.value.allowed for c in choices] == [True, True, False]
    assert choices[1].value.remember is True


def test_reject_choice_has_placeholder() -> None:
    """拒绝选项带有 placeholder，光标移上去时出现输入框。"""
    req = ApprovalRequest(heading="测试", question="是否？", key=None)
    choices = build_choices(req)
    reject = choices[-1]
    assert reject.placeholder is not None
    assert "拒绝" in reject.placeholder


# ── handler 桥接 ──────────────────────────────────────────


def test_handler_offers_remember_when_hook_supports_it() -> None:
    """hook 提供 can_remember/allow_always 时，卡片出现三个选项。"""

    async def go():
        app = ChatApp(FakeBackend(RememberingHook()), session_id="s")
        async with app.run_test() as pilot:
            handler = TuiApprovalHandler(app)
            req = ApprovalRequest(
                heading="Bash 命令",
                question="是否执行该命令？",
                fields=[("命令", "ls")],
                key="bash:ls",
            )
            task = asyncio.create_task(
                handler.request_approval(
                    ToolCall(id="c1", name="bash", input={"command": "ls"}), req
                )
            )
            await _wait_until(pilot, lambda: bool(app.query(ChoiceWidget)))
            widget = app.query_one(ChoiceWidget)
            count = len(widget.query(".choice-option"))
            await pilot.press("2")  # 选"是且不再询问"
            return count, await asyncio.wait_for(task, timeout=_TIMEOUT_SECONDS)

    count, result = asyncio.run(go())
    assert count == 3
    assert result.allowed is True


def test_handler_hides_remember_when_hook_lacks_support() -> None:
    """hook 无记忆能力时只有两个选项。"""

    async def go():
        app = ChatApp(FakeBackend(object()), session_id="s")
        async with app.run_test() as pilot:
            handler = TuiApprovalHandler(app)
            req = ApprovalRequest(
                heading="Bash 命令",
                question="是否执行该命令？",
                fields=[("命令", "ls")],
                key=None,
            )
            task = asyncio.create_task(
                handler.request_approval(
                    ToolCall(id="c1", name="bash", input={"command": "ls"}), req
                )
            )
            await _wait_until(pilot, lambda: bool(app.query(ChoiceWidget)))
            count = len(app.query_one(ChoiceWidget).query(".choice-option"))
            await pilot.press("escape")  # 选中"否"（末项 = escape_index）
            return count, await asyncio.wait_for(task, timeout=_TIMEOUT_SECONDS)

    count, result = asyncio.run(go())
    assert count == 2
    assert result.allowed is False
    assert result.reason == "用户拒绝"


def test_handler_renders_tool_heading_and_preview() -> None:
    """卡片文案由 ApprovalRequest 驱动。"""

    async def go():
        app = ChatApp(FakeBackend(object()), session_id="s")
        async with app.run_test() as pilot:
            handler = TuiApprovalHandler(app)
            req = ApprovalRequest(
                heading="Bash 命令",
                question="是否执行该命令？",
                fields=[("命令", "ls -la")],
                key="bash:ls -la",
            )
            task = asyncio.create_task(
                handler.request_approval(
                    ToolCall(id="c1", name="bash", input={"command": "ls -la"}), req
                )
            )
            await _wait_until(pilot, lambda: bool(app.query(ChoiceWidget)))
            widget = app.query_one(ChoiceWidget)
            head = str(widget.query_one("#choice-heading", Static).content)
            body = widget.query_one("#choice-body", Static).content.plain
            await pilot.press("1")  # 选"是"——直接批准
            await asyncio.wait_for(task, timeout=_TIMEOUT_SECONDS)
            return head, body

    head, body = asyncio.run(go())
    assert head == "Bash 命令"
    assert "ls -la" in body


def test_tui_approval_handler_stub() -> None:
    """TuiApprovalHandler 的审批 handler 协议正确实现。"""

    class StubHandler(ApprovalHandler):
        async def request_approval(
            self, call: ToolCall, request: ApprovalRequest
        ) -> ToolApproval:
            return ToolApproval(allowed=True, reason="桩通过")

    handler = StubHandler()

    async def go():
        return await handler.request_approval(
            ToolCall(id="c3", name="bash", input={"cmd": "ls"}),
            ApprovalRequest(heading="Bash", question="是否？"),
        )

    result = asyncio.run(go())
    assert result.allowed is True


# ── 拒绝原因（内联输入） ──────────────────────────────────


def test_reject_enter_without_text() -> None:
    """拒绝后不输入原因直接 Enter：返回"用户拒绝"。"""

    async def go():
        app = ChatApp(FakeBackend(), session_id="s")
        async with app.run_test() as pilot:
            handler = TuiApprovalHandler(app)
            req = ApprovalRequest(
                heading="Bash 命令",
                question="是否执行该命令？",
                fields=[("命令", "rm -rf /")],
                key="bash:rm -rf /",
            )
            task = asyncio.create_task(
                handler.request_approval(
                    ToolCall(id="c1", name="bash", input={"command": "rm -rf /"}),
                    req,
                )
            )
            await _wait_until(pilot, lambda: bool(app.query(ChoiceWidget)))
            # key 非空 → 3 个选项，光标移到末项（否）
            await pilot.press("down")
            await pilot.press("down")
            await _wait_until(pilot, lambda: bool(app.query("#choice-input")))
            await pilot.press("enter")  # 空内容提交
            return await asyncio.wait_for(task, timeout=_TIMEOUT_SECONDS)

    result = asyncio.run(go())
    assert result.allowed is False
    assert result.reason == "用户拒绝"


def test_reject_with_text() -> None:
    """拒绝后输入原因，Enter 提交：返回带原因的拒绝。"""

    async def go():
        app = ChatApp(FakeBackend(), session_id="s")
        async with app.run_test() as pilot:
            handler = TuiApprovalHandler(app)
            req = ApprovalRequest(
                heading="写入文件",
                question="是否写入该文件？",
                fields=[("文件", "/etc/hosts")],
                key="write:/etc/hosts",
            )
            task = asyncio.create_task(
                handler.request_approval(
                    ToolCall(
                        id="c2",
                        name="write",
                        input={"file_path": "/etc/hosts", "content": "..."},
                    ),
                    req,
                )
            )
            await _wait_until(pilot, lambda: bool(app.query(ChoiceWidget)))
            # key 非空 → 3 个选项，光标移到末项（否）
            await pilot.press("down")
            await pilot.press("down")
            await _wait_until(pilot, lambda: bool(app.query("#choice-input")))
            # 在内联输入框中输入原因
            inp = app.query_one("#choice-input", Input)
            inp.value = "不要修改系统文件"
            await pilot.press("enter")
            return await asyncio.wait_for(task, timeout=_TIMEOUT_SECONDS)

    result = asyncio.run(go())
    assert result.allowed is False
    assert result.reason == "用户拒绝了工具执行，原因为不要修改系统文件"


# ── 真实时序：回合进行中卡片必须可交互 ─────────────────────


class ApprovalBackend:
    """回合进行到一半调 hook.approve —— 复刻真实审批时序的后端桩。

    真实链路里 ``ConversationService.stream`` 在 Agent 循环内触发审批，
    审批 await 期间回合尚未结束；本桩把这一时序压缩成两条事件。
    """

    def __init__(self):
        self.hook = WorkspaceToolHook(
            Path("/nonexistent-workspace"), tools=_STANDARD_TOOLS
        )
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
            await pilot.press("down")  # key 非空，"不再询问"项存在，末项是第三项
            moved = widget.query_one("#choice-option-2", Static).content.plain
            # 此时光标在末项（拒绝），内联输入框出现
            await _wait_until(pilot, lambda: bool(app.query("#choice-input")))
            await pilot.press("enter")  # 空内容提交拒绝
            await _wait_until(pilot, lambda: backend.decision is not None)
            return moved, backend.decision

    moved, result = asyncio.run(asyncio.wait_for(go(), timeout=_TIMEOUT_SECONDS * 3))
    assert moved.startswith("❯ 3.")
    assert result is not None and result.allowed is False
