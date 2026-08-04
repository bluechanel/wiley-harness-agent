"""ChatApp TUI 冒烟:假后端驱动 headless ``run_test``,校验 Claude Code
风格界面的关键展示——启动横幅、用户/助手沟槽行、工具调用/输出折叠块、
用量行刷新与 /plan 本地命令。渲染纯函数的细节断言在 test_render.py,
这里只验证 app 层的组装与交互(仓库约定不引入 pytest-asyncio,外层
``asyncio.run`` 包装)。"""

import asyncio
import time

from textual.widgets import Collapsible, Input, Markdown, Static

from wy_core import TextDelta, ThinkingDelta, ToolCall, ToolResult, TurnEnd, Usage

from wy_coding_agent.reminders import PlanModeState
from wy_coding_agent.tui.app import ChatApp

_TIMEOUT_SECONDS = 5.0


class FakeBackend:
    """按脚本回放事件流的 ChatBackend 假实现。"""

    def __init__(self, events=()):
        self._events = tuple(events)
        self.plan_mode = PlanModeState()
        self.saved = 0
        self.inputs: list[str] = []

    async def stream(self, user_input: str):
        self.inputs.append(user_input)
        for event in self._events:
            yield event

    def save_state(self) -> None:
        self.saved += 1


async def _wait_until(pilot, condition) -> None:
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("等待界面状态超时")
        await pilot.pause(0.01)


def test_app_mounts_banner_on_start() -> None:
    async def go() -> str:
        app = ChatApp(
            FakeBackend(),
            session_id="abc-123",
            model_name="claude-x",
            workspace="/tmp/ws",
        )
        async with app.run_test() as pilot:
            await _wait_until(pilot, lambda: bool(app.query(".banner")))
            return str(app.query_one(".banner", Static).content)

    banner = asyncio.run(go())
    assert "claude-x" in banner
    assert "abc-123" in banner


def test_app_renders_turn_stream_and_updates_usage() -> None:
    events = [
        ThinkingDelta("想想"),
        TextDelta("先看看"),
        ToolCall(id="c1", name="bash", input={"command": "ls"}),
        ToolResult(id="c1", name="bash", content="a.txt\nb.txt", is_error=False),
        TextDelta("完成了"),
        TurnEnd(usage=Usage(input_tokens=10, output_tokens=5), context_tokens=500),
    ]
    backend = FakeBackend(events)

    async def go():
        app = ChatApp(backend, session_id="s", context_limit=1000)
        async with app.run_test() as pilot:
            prompt = app.query_one("#prompt", Input)
            await _wait_until(pilot, lambda: prompt.has_focus)
            await pilot.press("h", "i")
            await pilot.press("enter")
            await _wait_until(pilot, lambda: not prompt.disabled)
            collapsibles = [c.title for c in app.query(Collapsible)]
            answers = [
                str(m.source) for m in app.query(".assistant Markdown").results(Markdown)
            ]
            usage = str(app.query_one("#usage", Static).content)
            activity_running = app.query_one("#activity").has_class("running")
            return collapsibles, answers, usage, activity_running

    collapsibles, answers, usage, activity_running = asyncio.run(go())

    assert backend.inputs == ["hi"]
    # 思考、工具调用、工具输出各一个折叠块,标题是 Claude Code 式摘要。
    assert len(collapsibles) == 3
    assert collapsibles[0] == "思考过程"
    assert "bash" in collapsibles[1] and "(ls)" in collapsibles[1]
    assert "a.txt" in collapsibles[2] and "+1 行" in collapsibles[2]
    # 工具调用把回答切成两段沟槽行,各自独立渲染。
    assert answers == ["先看看", "完成了"]
    assert "距自动压缩 50%" in usage
    assert not activity_running


def test_app_plan_command_toggles_mode_and_hint() -> None:
    backend = FakeBackend()

    async def go() -> str:
        app = ChatApp(backend, session_id="s")
        async with app.run_test() as pilot:
            prompt = app.query_one("#prompt", Input)
            await _wait_until(pilot, lambda: prompt.has_focus)
            prompt.value = "/plan"
            await pilot.press("enter")
            await _wait_until(pilot, lambda: backend.plan_mode.active)
            return str(app.query_one("#status", Static).content)

    status = asyncio.run(go())
    assert backend.plan_mode.active
    assert backend.saved == 1
    assert "plan 模式" in status
