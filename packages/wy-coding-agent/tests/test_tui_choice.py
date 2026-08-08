"""内联单选组件冒烟测试：headless ``run_test`` 校验渲染与键盘交互。

组件与业务无关，这里只用无意义的字符串/整数当选项值；
工具审批那一层的文案与裁决映射在 ``test_tui_approval.py``。
"""

import asyncio
import time

import pytest
from textual.widgets import Static

from wy_coding_agent.tui.app import ChatApp
from wy_coding_agent.tui.choice import Choice, ChoiceWidget

_TIMEOUT_SECONDS = 5.0

_CHOICES = [Choice("甲", 1), Choice("乙", 2), Choice("丙", 3)]


class FakeBackend:
    """选择组件测试用的最简 ChatBackend 桩。"""

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


async def _mount(app, **kwargs):
    """挂一张选择卡片，返回 (widget, future)。"""
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    kwargs.setdefault("question", "选哪个？")
    kwargs.setdefault("choices", _CHOICES)
    widget = ChoiceWidget(future, **kwargs)
    await app._message_list.mount(widget)
    return widget, future


def _drive(keys, **kwargs):
    """挂卡片 → 依次按键 → 返回 future 结果。"""

    async def go():
        app = ChatApp(FakeBackend(), session_id="s")
        async with app.run_test() as pilot:
            widget, future = await _mount(app, **kwargs)
            await _wait_until(pilot, lambda: widget.has_focus)
            for key in keys:
                await pilot.press(key)
            return await asyncio.wait_for(future, timeout=_TIMEOUT_SECONDS)

    return asyncio.run(go())


# ── 键盘交互 ────────────────────────────────────────────────


def test_enter_selects_first_choice() -> None:
    assert _drive(["enter"]) == 1


def test_down_then_enter_selects_second() -> None:
    assert _drive(["down", "enter"]) == 2


def test_up_wraps_to_last() -> None:
    assert _drive(["up", "enter"]) == 3


def test_number_key_selects_directly() -> None:
    assert _drive(["3"]) == 3


def test_out_of_range_number_ignored() -> None:
    """越界数字键不应触发任何选择，后续 Enter 仍命中光标处。"""
    assert _drive(["9", "enter"]) == 1


def test_escape_hits_last_choice_by_default() -> None:
    assert _drive(["escape"]) == 3


def test_escape_index_is_configurable() -> None:
    assert _drive(["escape"], escape_index=0) == 1


# ── 渲染 ────────────────────────────────────────────────────


def test_renders_heading_body_question_and_cursor() -> None:
    async def go():
        app = ChatApp(FakeBackend(), session_id="s")
        async with app.run_test() as pilot:
            widget, future = await _mount(
                app, heading="标题", body="  正文", question="选哪个？"
            )
            await _wait_until(pilot, lambda: bool(widget.query("#choice-option-0")))

            def text(selector: str) -> str:
                return str(widget.query_one(selector, Static).content)

            def row(index: int) -> str:
                return widget.query_one(f"#choice-option-{index}", Static).content.plain

            rendered = (text("#choice-heading"), text("#choice-body"), row(0), row(1))
            await pilot.press("down")
            moved = (row(0), row(1))
            if not future.done():
                future.set_result(None)
            return rendered, moved

    (head, body, first, second), moved = asyncio.run(go())
    assert head == "标题"
    assert body == "  正文"
    assert first == "❯ 1. 甲"
    assert second == "  2. 乙"
    assert moved[0].startswith("  1.") and moved[1].startswith("❯ 2.")


def test_heading_and_body_omitted_when_empty() -> None:
    """不给标题/正文时不挂空 Static，卡片只剩问句与选项。"""

    async def go():
        app = ChatApp(FakeBackend(), session_id="s")
        async with app.run_test() as pilot:
            widget, future = await _mount(app)
            await _wait_until(pilot, lambda: bool(widget.query("#choice-options")))
            missing = (
                not widget.query("#choice-heading"),
                not widget.query("#choice-body"),
            )
            if not future.done():
                future.set_result(None)
            return missing

    assert asyncio.run(go()) == (True, True)


def test_widget_removed_after_choice() -> None:
    """选完卡片自行移除，不在会话流里留残影。"""

    async def go():
        app = ChatApp(FakeBackend(), session_id="s")
        async with app.run_test() as pilot:
            widget, future = await _mount(app)
            await _wait_until(pilot, lambda: widget.has_focus)
            await pilot.press("enter")
            await asyncio.wait_for(future, timeout=_TIMEOUT_SECONDS)
            await _wait_until(pilot, lambda: not app.query(ChoiceWidget))
            return True

    assert asyncio.run(go())


# ── 契约 ────────────────────────────────────────────────────


def test_empty_choices_rejected() -> None:
    async def go():
        future = asyncio.get_event_loop().create_future()
        with pytest.raises(ValueError):
            ChoiceWidget(future, question="选哪个？", choices=[])
        future.cancel()

    asyncio.run(go())


def test_ask_choice_returns_selected_value() -> None:
    """``ChatApp.ask_choice`` 是对外入口：返回所选 Choice.value。"""

    async def go():
        app = ChatApp(FakeBackend(), session_id="s")
        async with app.run_test() as pilot:
            task = asyncio.create_task(
                app.ask_choice(question="选哪个？", choices=_CHOICES, heading="标题")
            )
            await _wait_until(pilot, lambda: bool(app.query(ChoiceWidget)))
            await pilot.press("2")
            return await asyncio.wait_for(task, timeout=_TIMEOUT_SECONDS)

    assert asyncio.run(go()) == 2
