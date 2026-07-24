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


def test_tool_call_markdown_renders_arguments_as_json() -> None:
    markdown = render.tool_call_markdown("bash", {"command": "ls -la"})
    assert markdown.startswith("### 工具调用：bash")
    assert '"command": "ls -la"' in markdown
    assert "```json" in markdown


def test_tool_output_markdown_marks_errors() -> None:
    markdown = render.tool_output_markdown("bash", "Error: denied", is_error=True)
    assert "### 工具输出（错误）：bash" in markdown
    assert "Error: denied" in markdown


def test_tool_output_markdown_extends_fence_past_backticks() -> None:
    markdown = render.tool_output_markdown("bash", "```python\nprint()\n```")
    assert "````\n```python" in markdown


def test_tool_output_markdown_clips_long_output() -> None:
    output = "\n".join(f"line {i}" for i in range(100))
    markdown = render.tool_output_markdown("bash", output)
    assert "line 0" in markdown
    assert "line 99" not in markdown
    assert "已截断" in markdown
    assert "100 行" in markdown


def test_tool_output_markdown_handles_empty_output() -> None:
    assert "（空）" in render.tool_output_markdown("bash", "")


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
    assert "### 工具调用：bash" in call_views[0].markdown

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
    assert output_views[0].classes == "tool"
    assert "### 工具输出（错误）：bash" in output_views[0].markdown


def test_render_record_answer_no_longer_emits_usage_view() -> None:
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
    assert "### 助手" in views[0].markdown
