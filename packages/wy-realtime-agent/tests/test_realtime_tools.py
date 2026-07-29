"""Tests for the built-in read tool (复刻件) and the MCP result translation."""

from pathlib import Path

import pytest

from wy_realtime_agent.tools import DEFAULT_TOOLS
from wy_realtime_agent.tools.mcp_tool import result_to_text
from wy_realtime_agent.tools.read import READ, FileToolError


def test_default_tools_contains_only_read() -> None:
    assert [tool.name for tool in DEFAULT_TOOLS] == ["read"]


def test_read_formats_lines_cat_n_style(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("alpha\nbeta\n", encoding="utf-8")

    assert READ.execute({"file_path": str(path)}) == "     1\talpha\n     2\tbeta"


def test_read_normalizes_crlf(tmp_path: Path) -> None:
    path = tmp_path / "crlf.txt"
    path.write_bytes(b"a\r\nb\r\n")

    assert READ.execute({"file_path": str(path)}) == "     1\ta\n     2\tb"


def test_read_offset_limit_and_continuation_hint(tmp_path: Path) -> None:
    path = tmp_path / "lines.txt"
    path.write_text("\n".join(f"line{i}" for i in range(1, 11)), encoding="utf-8")

    output = READ.execute({"file_path": str(path), "offset": 2, "limit": 3})

    assert output.startswith("     2\tline2")
    assert "     4\tline4" in output
    assert "Use offset: 5 to continue" in output


def test_read_empty_file_and_offset_past_end(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")
    assert "contents are empty" in READ.execute({"file_path": str(path)})

    short = tmp_path / "short.txt"
    short.write_text("one\n", encoding="utf-8")
    assert "shorter than the provided offset" in READ.execute(
        {"file_path": str(short), "offset": 10}
    )


def test_read_missing_file_suggests_similar_sibling(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("x", encoding="utf-8")

    with pytest.raises(FileToolError, match="Did you mean") as excinfo:
        READ.execute({"file_path": str(tmp_path / "notes.txt")})
    assert "notes.md" in str(excinfo.value)


def test_read_rejects_binary_and_directory_and_bad_args(tmp_path: Path) -> None:
    with pytest.raises(FileToolError, match="binary"):
        READ.execute({"file_path": str(tmp_path / "pic.png")})
    with pytest.raises(FileToolError, match="directory"):
        READ.execute({"file_path": str(tmp_path)})
    with pytest.raises(FileToolError, match="file_path"):
        READ.execute({})
    with pytest.raises(FileToolError, match="offset"):
        READ.execute({"file_path": str(tmp_path / "a.txt"), "offset": 0})


# --- result_to_text(MCP 结果翻译,行为对齐 wy-coding-agent 复刻源) ---


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Result:
    def __init__(self, content: list, structured: object = None) -> None:
        self.content = content
        self.structuredContent = structured
        self.isError = False


def test_result_to_text_joins_text_blocks() -> None:
    assert result_to_text(_Result([_Block("a"), _Block("b")])) == "a\nb"


def test_result_to_text_falls_back_to_structured_content() -> None:
    assert result_to_text(_Result([], structured={"answer": 3})) == '{"answer": 3}'
