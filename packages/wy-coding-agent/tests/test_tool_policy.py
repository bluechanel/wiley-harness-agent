"""tool_policy 模块:审批策略 Hook 单元测试。"""

import asyncio
import tempfile
from pathlib import Path

from wy_core import (
    Agent,
    Message,
    Model,
    ModelEnd,
    ModelEvent,
    TextBlock,
    Tool,
    ToolApproval,
    ToolCall,
    ToolResult,
    ToolUseBlock,
    TurnEnd,
    Usage,
)

from wy_coding_agent.tool_policy import ApprovalHandler, WorkspaceToolHook


# ── 测试辅助 ────────────────────────────────────────────────


def _asyncio_run(coro):
    return asyncio.run(coro)


def _end_event(*blocks, usage=None, stop_reason="end_turn"):
    return ModelEnd(
        message=Message(role="assistant", content=list(blocks)),
        usage=usage or Usage(input_tokens=10, output_tokens=5),
        stop_reason=stop_reason,
    )


def _text_end(text, **kwargs):
    return _end_event(TextBlock(text), **kwargs)


class _FakeModel(Model):
    name = "fake"

    def __init__(self, scripts: list[list[ModelEvent]]):
        self.scripts = list(scripts)

    async def stream(self, messages, *, system=None, tools=None):
        for event in self.scripts.pop(0):
            if isinstance(event, BaseException):
                raise event
            yield event


class _EchoTool(Tool):
    name = "echo"
    description = "echo"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}

    def execute(self, input: dict) -> str:
        return str(input.get("text", ""))


def _run_events(agent: Agent, prompt: str) -> list:
    async def go():
        return [e async for e in agent.run(prompt)]

    return asyncio.run(go())


class FakeApprovalHandler(ApprovalHandler):
    """测试用:记录调用并返回预设决定。"""

    def __init__(self, decision: ToolApproval | None = None):
        self.calls: list[ToolCall] = []
        self._decision = decision

    async def request_approval(self, call: ToolCall) -> ToolApproval:
        self.calls.append(call)
        if self._decision is not None:
            return self._decision
        return ToolApproval(allowed=True, reason="测试通过")


# ── 策略:无需审批直接放行 ──────────────────────────────────


def test_工作区内文件直接放行():
    ws = Path(tempfile.mkdtemp())
    f = ws / "foo.py"
    f.write_text("hello")
    handler = FakeApprovalHandler()
    hook = WorkspaceToolHook(ws, handler=handler)

    result = _asyncio_run(
        hook.approve(ToolCall(id="c1", name="read", input={"file_path": str(f)}))
    )
    assert result.allowed is True
    assert len(handler.calls) == 0  # 未走审批


def test_其他工具默认放行():
    hook = WorkspaceToolHook(Path("/tmp"), handler=FakeApprovalHandler())
    for name in ("glob", "grep", "agent", "skill", "exit_plan_mode", "mcp__srv__tool"):
        result = _asyncio_run(
            hook.approve(ToolCall(id="x", name=name, input={}))
        )
        assert result.allowed is True, f"{name} 应该放行"


# ── 策略:需要审批 ──────────────────────────────────────────


def test_bash_无handler时拒绝():
    hook = WorkspaceToolHook(Path("/tmp"))
    result = _asyncio_run(
        hook.approve(ToolCall(id="c1", name="bash", input={"command": "ls"}))
    )
    assert result.allowed is False
    assert "bash" in result.reason


def test_bash_有handler时委托():
    handler = FakeApprovalHandler(
        decision=ToolApproval(allowed=True, reason="ok")
    )
    hook = WorkspaceToolHook(Path("/tmp"), handler=handler)
    result = _asyncio_run(
        hook.approve(ToolCall(id="c1", name="bash", input={"command": "ls"}))
    )
    assert result.allowed is True
    assert len(handler.calls) == 1
    assert handler.calls[0].name == "bash"


def test_read_工作区外文件_无handler时拒绝():
    hook = WorkspaceToolHook(Path("/tmp/ws"))
    result = _asyncio_run(
        hook.approve(
            ToolCall(id="c1", name="read", input={"file_path": "/etc/passwd"})
        )
    )
    assert result.allowed is False


def test_write_工作区外文件_有handler时委托():
    handler = FakeApprovalHandler()
    hook = WorkspaceToolHook(Path("/tmp/ws"), handler=handler)
    result = _asyncio_run(
        hook.approve(
            ToolCall(id="c1", name="write", input={"file_path": "/etc/foo"})
        )
    )
    assert result.allowed is True
    assert len(handler.calls) == 1
    assert handler.calls[0].name == "write"


# ── 路径处理边缘情况 ──────────────────────────────────────


def test_文件路径缺失时需审批():
    hook = WorkspaceToolHook(Path("/tmp/ws"))
    result = _asyncio_run(
        hook.approve(ToolCall(id="c1", name="edit", input={"other": "val"}))
    )
    assert result.allowed is False


def test_空文件路径时需审批():
    hook = WorkspaceToolHook(Path("/tmp/ws"))
    result = _asyncio_run(
        hook.approve(ToolCall(id="c1", name="read", input={"file_path": ""}))
    )
    assert result.allowed is False


def test_相对路径支持():
    import os

    ws = Path(tempfile.mkdtemp())
    f = ws / "bar.txt"
    f.write_text("ok")
    hook = WorkspaceToolHook(ws, handler=FakeApprovalHandler())
    rel = os.path.relpath(str(f), str(Path.cwd()))
    result = _asyncio_run(
        hook.approve(ToolCall(id="c1", name="read", input={"file_path": rel}))
    )
    assert result.allowed is True


# ── handler 注入 ───────────────────────────────────────────


def test_set_handler_替换handler():
    hook = WorkspaceToolHook(Path("/tmp"))
    # 无 handler → bash 被拒
    r1 = _asyncio_run(hook.approve(ToolCall(id="c1", name="bash", input={})))
    assert r1.allowed is False

    # 注入 handler → bash 被批准
    handler = FakeApprovalHandler()
    hook.set_handler(handler)
    r2 = _asyncio_run(hook.approve(ToolCall(id="c1", name="bash", input={})))
    assert r2.allowed is True
    assert len(handler.calls) == 1

    # 移除 handler → bash 又被拒
    hook.set_handler(None)
    r3 = _asyncio_run(hook.approve(ToolCall(id="c1", name="bash", input={})))
    assert r3.allowed is False


# ── Agent 集成测试 ─────────────────────────────────────────


def test_agent_bash被审批拒绝():
    model = _FakeModel(
        [
            [
                _end_event(
                    ToolUseBlock(id="t1", name="bash", input={}),
                    stop_reason="tool_use",
                )
            ],
            [_text_end("好")],
        ]
    )
    agent = Agent(
        model=model,
        tools=[_EchoTool()],
        tool_hook=WorkspaceToolHook(Path("/tmp")),
        audit=None,
    )
    events = _run_events(agent, "运行 bash")
    result = events[1]
    assert isinstance(result, ToolResult) and result.is_error
    assert "bash" in result.content


def test_agent_read工作区内文件放行():
    ws = Path(tempfile.mkdtemp())
    f = ws / "test.txt"
    f.write_text("hello")

    model = _FakeModel(
        [
            [
                _end_event(
                    ToolUseBlock(id="t1", name="echo", input={"text": "abc"}),
                    stop_reason="tool_use",
                )
            ],
            [_text_end("完成")],
        ]
    )
    agent = Agent(
        model=model,
        tools=[_EchoTool()],
        tool_hook=WorkspaceToolHook(ws, handler=FakeApprovalHandler()),
        audit=None,
    )
    events = _run_events(agent, "读文件")
    assert [type(e) for e in events] == [ToolCall, ToolResult, TurnEnd]


def test_agent_无hook时行为不变():
    model = _FakeModel(
        [
            [
                _end_event(
                    ToolUseBlock(id="t1", name="echo", input={"text": "abc"}),
                    stop_reason="tool_use",
                )
            ],
            [_text_end("完成")],
        ]
    )
    agent = Agent(model=model, tools=[_EchoTool()], audit=None)
    events = _run_events(agent, "调用")
    assert [type(e) for e in events] == [ToolCall, ToolResult, TurnEnd]
    assert events[1].content == "abc" and not events[1].is_error


# ── 记住选择（"不再询问"） ─────────────────────────────────


def test_allow_always_后同一命令不再走审批():
    handler = FakeApprovalHandler()
    hook = WorkspaceToolHook(Path("/tmp"), handler=handler)
    call = ToolCall(id="c1", name="bash", input={"command": "ls -la"})

    assert hook.can_remember(call) is True
    hook.allow_always(call)
    result = _asyncio_run(hook.approve(call))
    assert result.allowed is True
    assert len(handler.calls) == 0  # 命中记忆，未打扰 handler


def test_allow_always_不放宽到其他命令():
    handler = FakeApprovalHandler()
    hook = WorkspaceToolHook(Path("/tmp"), handler=handler)
    hook.allow_always(ToolCall(id="c1", name="bash", input={"command": "ls"}))

    _asyncio_run(
        hook.approve(ToolCall(id="c2", name="bash", input={"command": "ls -la"}))
    )
    assert len(handler.calls) == 1  # 命令不同，仍需审批


def test_allow_always_按路径记住工作区外文件():
    ws = Path(tempfile.mkdtemp())
    outside = Path(tempfile.mkdtemp()) / "x.txt"
    outside.write_text("hi")
    handler = FakeApprovalHandler()
    hook = WorkspaceToolHook(ws, handler=handler)
    call = ToolCall(id="c1", name="write", input={"file_path": str(outside)})

    hook.allow_always(call)
    result = _asyncio_run(hook.approve(call))
    assert result.allowed is True
    assert len(handler.calls) == 0


def test_can_remember_对无标识调用为假():
    hook = WorkspaceToolHook(Path("/tmp"))
    assert hook.can_remember(ToolCall(id="c1", name="bash", input={})) is False
    assert hook.can_remember(ToolCall(id="c2", name="read", input={})) is False
    assert hook.can_remember(ToolCall(id="c3", name="grep", input={"p": "x"})) is False
