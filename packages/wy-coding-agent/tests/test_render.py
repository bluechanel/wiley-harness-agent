from wy_core import Usage

from wy_coding_agent import SessionRecord
from wy_coding_agent.tui import render


def _record(role, content, *, kind=None, metadata=None) -> SessionRecord:
    return SessionRecord(
        timestamp="2026-01-01T00:00:00+00:00",
        session_id="s",
        role=role,
        content=content,
        kind=kind,
        metadata=metadata,
    )


def test_usage_bar_text_shows_compact_totals_and_context() -> None:
    total = Usage(
        input_tokens=1200,
        output_tokens=34,
        cache_write_tokens=5,
        cache_read_tokens=6000,
    )
    text = render.usage_bar_text(total, 7890)
    assert text == "输入 1.2k · 输出 34 · 缓存 6.0k · 上下文 7.9k"


def test_usage_bar_text_with_limit_appends_compaction_headroom() -> None:
    text = render.usage_bar_text(Usage(), 7890, 150_000)
    assert text.endswith("距自动压缩 94%")


def test_usage_bar_text_headroom_never_negative() -> None:
    text = render.usage_bar_text(Usage(), 200_000, 150_000)
    assert text.endswith("距自动压缩 0%")


def test_user_view_is_raw_text_with_prompt_gutter() -> None:
    view = render.user_view("hello **world**")
    assert view.text == "hello **world**"
    assert view.symbol == ">"
    assert view.classes == "user"
    assert view.markdown == ""


def test_answer_view_is_markdown_with_dot_gutter() -> None:
    view = render.answer_view("hi")
    assert view.markdown == "hi"
    assert view.symbol == "⏺"
    assert view.classes == "assistant"


def test_error_view_is_raw_text_with_dot_gutter() -> None:
    view = render.error_view(RuntimeError("boom [x]"))
    assert view.text == "boom [x]"
    assert view.symbol == "⏺"
    assert view.classes == "error"


def test_reasoning_view_is_collapsible_with_raw_text() -> None:
    view = render.reasoning_view("thinking hard")
    assert view.collapsible_title == "思考过程"
    assert view.symbol == "✻"
    assert view.classes == "thinking"
    assert view.markdown == "thinking hard"


def test_tool_call_view_summarizes_primary_argument() -> None:
    view = render.tool_call_view("bash", {"command": "ls -la"})
    assert "bash" in view.collapsible_title
    assert "(ls -la)" in view.collapsible_title
    assert view.symbol == "⏺"
    assert view.classes == "tool-call"
    assert '"command": "ls -la"' in view.markdown


def test_tool_call_view_escapes_markup_in_summary() -> None:
    view = render.tool_call_view("grep", {"pattern": "[a-z]+"})
    assert "\\[a-z]+" in view.collapsible_title


def test_tool_call_view_clips_long_summary_to_one_line() -> None:
    view = render.tool_call_view("bash", {"command": "echo a\n" + "x" * 200})
    assert "…" in view.collapsible_title
    assert "\n" not in view.collapsible_title


def test_tool_call_view_falls_back_to_compact_json() -> None:
    view = render.tool_call_view("agent", {"steps": 3})
    assert '{"steps": 3}' in view.collapsible_title


def test_exit_plan_mode_call_renders_plan_expanded() -> None:
    view = render.tool_call_view("exit_plan_mode", {"plan": "## 方案\n1. 做事"})
    assert view.collapsible_title == ""  # 计划直接展开供审阅,不折叠
    assert view.classes == "plan"
    assert view.markdown == "## 方案\n1. 做事"


def test_exit_plan_mode_call_empty_plan_falls_back() -> None:
    view = render.tool_call_view("exit_plan_mode", {"plan": " "})
    assert view.classes == "tool-call"
    assert "exit_plan_mode" in view.collapsible_title


def test_tool_output_view_previews_first_line_and_count() -> None:
    output = "\n".join(f"line {i}" for i in range(100))
    view = render.tool_output_view("bash", output)
    assert view.collapsible_title == "line 0 (+99 行)"
    assert view.symbol == "⎿"
    assert view.classes == "tool-result"


def test_tool_output_view_marks_errors() -> None:
    view = render.tool_output_view("bash", "Error: denied", is_error=True)
    assert view.classes == "tool-result error"
    assert view.collapsible_title == "Error: denied"


def test_tool_output_view_handles_empty_output() -> None:
    view = render.tool_output_view("bash", "")
    assert view.collapsible_title == "(无输出)"
    assert "（空）" in view.markdown


def test_tool_output_view_extends_fence_past_backticks() -> None:
    view = render.tool_output_view("bash", "```python\nprint()\n```")
    assert "````\n```python" in view.markdown


def test_tool_output_view_clips_long_output_body() -> None:
    output = "\n".join(f"line {i}" for i in range(100))
    view = render.tool_output_view("bash", output)
    assert "line 0" in view.markdown
    assert "line 99" not in view.markdown
    assert "已截断" in view.markdown
    assert "100 行" in view.markdown


def test_compaction_view_is_collapsible_with_summary() -> None:
    view = render.compaction_view(6, "早前对话的摘要")
    assert view.collapsible_title == "上下文已压缩 · 总结了 6 条早前消息"
    assert view.symbol == "✽"
    assert "早前对话的摘要" in view.markdown


def test_banner_text_lists_model_workspace_session() -> None:
    text = render.banner_text("claude-x", "/tmp/ws", "abc-123")
    assert "欢迎使用" in text
    assert "claude-x" in text
    assert "/tmp/ws" in text
    assert "abc-123" in text


def test_banner_text_skips_missing_details() -> None:
    text = render.banner_text("", "", "")
    assert "模型" not in text
    assert "欢迎使用" in text


def test_hint_text_shows_plan_mode_indicator() -> None:
    assert "plan 模式" in render.hint_text(True)
    assert "⏸" in render.hint_text(True)
    assert "/plan" in render.hint_text(False)


def test_spinner_text_cycles_frames_with_elapsed_seconds() -> None:
    first = render.spinner_text(0, "思考中", 3.7)
    second = render.spinner_text(1, "思考中", 3.7)
    assert first != second
    assert "思考中" in first
    assert "(3s)" in first


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
    assert call_views[0].classes == "tool-call"
    assert "bash" in call_views[0].collapsible_title
    assert "(ls)" in call_views[0].collapsible_title

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
    assert output_views[0].classes == "tool-result error"
    assert output_views[0].collapsible_title == "Error: denied"


def test_render_record_thinking_is_collapsible() -> None:
    views = render.render_record(
        _record("assistant", "step by step", kind="thinking")
    )
    assert len(views) == 1
    assert views[0].collapsible_title == "思考过程"
    assert views[0].markdown == "step by step"


def test_render_record_skips_blank_assistant_records() -> None:
    assert render.render_record(_record("assistant", " ", kind="thinking")) == []
    assert render.render_record(_record("assistant", "", kind="answer")) == []


def test_render_record_compaction() -> None:
    views = render.render_record(
        _record("assistant", "摘要内容", kind="compaction", metadata={"dropped": 4})
    )
    assert len(views) == 1
    assert views[0].collapsible_title == "上下文已压缩 · 总结了 4 条早前消息"
    assert "摘要内容" in views[0].markdown


def test_render_record_answer_stays_expanded_with_dot_gutter() -> None:
    record = SessionRecord(
        timestamp="2026-01-01T00:00:00+00:00",
        session_id="s",
        role="assistant",
        content="hello",
        kind="answer",
        usage=Usage(input_tokens=1),
        total_usage=Usage(input_tokens=1),
    )
    views = render.render_record(record)
    assert len(views) == 1
    assert views[0].collapsible_title == ""
    assert views[0].symbol == "⏺"
    assert views[0].markdown == "hello"


def test_render_record_user_is_raw_text() -> None:
    views = render.render_record(_record("user", "帮我改代码", kind="input"))
    assert len(views) == 1
    assert views[0].text == "帮我改代码"
    assert views[0].symbol == ">"
