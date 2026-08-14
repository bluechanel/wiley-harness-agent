"""tool_search 工具:直选、关键词检索、渲染与激活效果。"""

import json

import pytest

from wy_core import Tool, ToolSet

from wy_coding_agent.tools.tool_search import ToolSearchTool

from app_helpers import EchoTool


class _Deferred(Tool):
    deferred = True

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self.parameters = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }

    def execute(self, input: dict) -> str:  # pragma: no cover - 本测试不执行
        return "ok"


def _toolset() -> ToolSet:
    toolset = ToolSet(
        [
            EchoTool(),
            _Deferred("mcp__slack__send_message", "Send a message to a Slack channel"),
            _Deferred("mcp__slack__list_channels", "List Slack channels"),
            _Deferred("mcp__github__create_issue", "Create an issue on GitHub"),
            _Deferred("NotebookEdit", "Edit a jupyter notebook cell"),
        ]
    )
    toolset.add(ToolSearchTool(toolset))
    return toolset


def _search(toolset: ToolSet, query: str, **extra) -> str:
    tool = toolset.get("tool_search")
    assert isinstance(tool, ToolSearchTool)
    return tool.execute({"query": query, **extra})


def test_select_loads_named_tools_and_returns_schemas() -> None:
    toolset = _toolset()

    result = _search(toolset, "select:mcp__slack__send_message,NotebookEdit")

    assert "<functions>" in result and "</functions>" in result
    definitions = [
        json.loads(line[len("<function>") : -len("</function>")])
        for line in result.splitlines()
        if line.startswith("<function>")
    ]
    assert [d["name"] for d in definitions] == [
        "mcp__slack__send_message",
        "NotebookEdit",
    ]
    assert definitions[0]["description"] == "Send a message to a Slack channel"
    assert definitions[0]["parameters"]["required"] == ["x"]
    # 命中即加载:后续请求会带上它们
    available = {t.name for t in toolset.available}
    assert {"mcp__slack__send_message", "NotebookEdit"} <= available
    assert [t.name for t in toolset.deferred] == [
        "mcp__slack__list_channels",
        "mcp__github__create_issue",
    ]


def test_select_is_idempotent_and_skips_unknown_names() -> None:
    toolset = _toolset()

    _search(toolset, "select:NotebookEdit")
    result = _search(toolset, "select:NotebookEdit, nope ,echo")

    assert "NotebookEdit" in result
    assert "echo" in result  # 已加载的工具"再选一次"是无害的幂等操作
    assert "nope" not in result


def test_select_without_any_known_name_reports_no_match() -> None:
    toolset = _toolset()

    result = _search(toolset, "select:nope,also_nope")

    assert "No matching deferred tools found" in result
    assert "mcp__slack__send_message" in result  # 附上待加载清单便于重试


def test_keyword_search_ranks_name_matches_first() -> None:
    toolset = _toolset()

    result = _search(toolset, "slack")

    names = [line for line in result.splitlines() if line.startswith("<function>")]
    assert len(names) == 2
    assert "mcp__slack__send_message" in names[0] or "mcp__slack__list_channels" in names[0]
    assert all("slack" in line for line in names)


def test_keyword_search_matches_description() -> None:
    toolset = _toolset()

    result = _search(toolset, "jupyter")

    assert "NotebookEdit" in result
    assert "slack" not in result


def test_required_terms_narrow_the_candidate_set() -> None:
    toolset = _toolset()

    result = _search(toolset, "+slack send")

    lines = [line for line in result.splitlines() if line.startswith("<function>")]
    # 必含词只筛掉不含 slack 的工具;可选词 send 决定排序
    assert "mcp__github__create_issue" not in result
    assert "mcp__slack__send_message" in lines[0]
    assert len(lines) == 2


def test_required_term_absent_everywhere_yields_no_match() -> None:
    toolset = _toolset()

    result = _search(toolset, "+quantum slack")

    assert "No matching deferred tools found" in result


def test_mcp_prefix_query_matches_whole_server() -> None:
    toolset = _toolset()

    result = _search(toolset, "mcp__slack")

    assert "mcp__slack__send_message" in result
    assert "mcp__slack__list_channels" in result
    assert "mcp__github__create_issue" not in result


def test_bare_tool_name_query_takes_the_fast_path() -> None:
    toolset = _toolset()

    result = _search(toolset, "NotebookEdit")

    assert result.count("<function>") == 1
    assert "NotebookEdit" in result


def test_max_results_caps_keyword_matches() -> None:
    toolset = _toolset()

    assert _search(toolset, "slack").count("<function>") == 2
    assert _search(_toolset(), "slack", max_results=1).count("<function>") == 1


def test_no_match_lists_pending_tools() -> None:
    toolset = _toolset()

    result = _search(toolset, "quantum teleportation")

    assert "No matching deferred tools found" in result
    assert "mcp__github__create_issue" in result
    assert [t.name for t in toolset.deferred]  # 无命中不改变激活状态


def test_empty_query_is_rejected() -> None:
    toolset = _toolset()

    with pytest.raises(ValueError):
        _search(toolset, "   ")
