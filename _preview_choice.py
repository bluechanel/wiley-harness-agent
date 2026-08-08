"""临时脱机预览：把选择卡片渲染成纯文本，肉眼核对版式。"""

import asyncio

from wy_core import ToolCall

from wy_coding_agent.tui.app import ChatApp
from wy_coding_agent.tui.approval import TuiApprovalHandler
from wy_coding_agent.tui.choice import Choice, ChoiceWidget


class FakeBackend:
    def __init__(self, hook=None):
        self.tool_hook = hook
        self.plan_mode = None

    async def stream(self, user_input):
        if False:
            yield

    def save_state(self):
        pass


class Hook:
    def can_remember(self, call):
        return True

    def allow_always(self, call):
        pass


def dump(app):
    strips = app.screen._compositor.render_strips()
    lines = ["".join(seg.text for seg in strip).rstrip() for strip in strips]
    out = []
    for ln in lines:
        if not ln.strip():
            continue
        if "欢迎使用" in ln or "想让我做什么" in ln or "Enter 发送" in ln:
            break
        out.append(ln)
    print("\n".join(out))
    print("=" * 76)


async def main():
    # 1) 工具审批（经 handler，走真实链路）
    for call in [
        ToolCall(id="1", name="bash", input={"command": "uv run pytest -q", "description": "跑全部测试"}),
        ToolCall(
            id="2",
            name="edit",
            input={
                "file_path": "packages/wy-coding-agent/src/wy_coding_agent/tui/app.py",
                "old_string": "border: round #565656;",
                "new_string": "border: round #D77757;",
            },
        ),
    ]:
        app = ChatApp(FakeBackend(Hook()), session_id="s")
        async with app.run_test(size=(78, 30)) as pilot:
            task = asyncio.create_task(TuiApprovalHandler(app).request_approval(call))
            while not app.query(ChoiceWidget):
                await pilot.pause(0.01)
            await pilot.pause()
            dump(app)
            await pilot.press("escape")
            await task

    # 2) 任意用途的选择（组件直接用，无标题）
    app = ChatApp(FakeBackend(), session_id="s")
    async with app.run_test(size=(78, 30)) as pilot:
        task = asyncio.create_task(
            app.ask_choice(
                question="检测到未提交的改动，要怎么处理？",
                choices=[
                    Choice("提交后继续", "commit"),
                    Choice("暂存后继续", "stash"),
                    Choice("先不动，取消本次操作 (esc)", "cancel"),
                ],
            )
        )
        while not app.query(ChoiceWidget):
            await pilot.pause(0.01)
        await pilot.pause()
        dump(app)
        await pilot.press("1")
        print("选择结果:", await task)


asyncio.run(main())
