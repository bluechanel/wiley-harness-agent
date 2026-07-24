from wiley_harness_agent.agent import ChatUsage, SessionRecord
from wiley_harness_agent.tui import render


def _record(role, content, *, kind=None, metadata=None) -> SessionRecord:
    return SessionRecord(
        timestamp="2026-01-01T00:00:00+00:00",
        session_id="s",
        role=role,
        content=content,
        kind=kind,
        metadata=metadata,
    )


def test_usage_bar_text_shows_totals_and_context() -> None:
    total = ChatUsage(
        input_tokens=1200,
        output_tokens=34,
        cache_creation_input_tokens=5,
        cache_read_input_tokens=6000,
    )
    text = render.usage_bar_text(total, 7890)
    assert text == "输入 1,200 · 输出 34 · 缓存 6,005 · 上下文 7,890 tokens"


def test_reasoning_view_is_collapsible_with_raw_text() -> None:
    view = render.reasoning_view("thinking hard")
    assert view.collapsible_title == "思考过程"
    assert view.classes == "reasoning"
    assert view.markdown == "thinking hard"


def test_tool_call_view_renders_arguments_as_json() -> None:
    view = render.tool_call_view("bash", {"command": "ls -la"})
    assert view.collapsible_title == "工具调用：bash"
    assert view.classes == "tool"
    assert '"command": "ls -la"' in view.markdown
    assert "```json" in view.markdown


def test_tool_output_view_marks_errors() -> None:
    view = render.tool_output_view("bash", "Error: denied", is_error=True)
    assert view.collapsible_title == "工具输出（错误）：bash"
    assert "Error: denied" in view.markdown


def test_tool_output_view_extends_fence_past_backticks() -> None:
    view = render.tool_output_view("bash", "```python\nprint()\n```")
    assert "````\n```python" in view.markdown


def test_tool_output_view_clips_long_output() -> None:
    output = "\n".join(f"line {i}" for i in range(100))
    view = render.tool_output_view("bash", output)
    assert "line 0" in view.markdown
    assert "line 99" not in view.markdown
    assert "已截断" in view.markdown
    assert "100 行" in view.markdown


def test_tool_output_view_handles_empty_output() -> None:
    assert "（空）" in render.tool_output_view("bash", "").markdown


def test_render_record_tool_call_and_output() -> None:
    call_views = render.render_record(
        _record(
            "tool_call",
            {"command": "ls"},
            kind="tool_call",
            metadata={"tool_name": "bash", "tool_call_id": "call-1"},
        )
    )
    assert len(call_views) == 1
    assert call_views[0].classes == "tool"
    assert call_views[0].collapsible_title == "工具调用：bash"

    output_views = render.render_record(
        _record(
            "tool_output",
            "Error: denied",
            kind="tool_output",
            metadata={
                "tool_name": "bash",
                "tool_call_id": "call-1",
                "is_error": True,
            },
        )
    )
    assert output_views[0].collapsible_title == "工具输出（错误）：bash"
    assert "Error: denied" in output_views[0].markdown


def test_render_record_thinking_is_collapsible() -> None:
    views = render.render_record(
        _record("assistant", "step by step", kind="thinking")
    )
    assert len(views) == 1
    assert views[0].collapsible_title == "思考过程"
    assert views[0].markdown == "step by step"


def test_render_record_answer_stays_expanded_without_usage_view() -> None:
    record = SessionRecord(
        timestamp="2026-01-01T00:00:00+00:00",
        session_id="s",
        role="assistant",
        content="hello",
        kind="answer",
        usage=ChatUsage(input_tokens=1),
        total_usage=ChatUsage(input_tokens=1),
    )
    views = render.render_record(record)
    assert len(views) == 1
    assert views[0].collapsible_title == ""
    assert "### 助手" in views[0].markdown
