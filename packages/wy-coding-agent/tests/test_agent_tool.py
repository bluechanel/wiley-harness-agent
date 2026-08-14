"""agent 工具:子 agent 派发、结果提取、审计文件与 factory 装配。"""

import json
from pathlib import Path

import pytest

from wy_core import ModelError, TextBlock, Tool, ToolResult, ToolUseBlock

from wy_coding_agent import create_agent
from wy_coding_agent.tools.agent import AgentTool

from app_helpers import EchoTool, FakeModel, drain, end_event, make_text_end


class _LazyTool(Tool):
    """懒加载工具:默认不随请求发送,需经 tool_search 加载。"""

    name = "lazy"
    description = "懒加载的示例工具"
    parameters = {"type": "object", "properties": {}}
    deferred = True

    def execute(self, input: dict) -> str:
        return "lazy ok"


def _tool_use(name: str, input: dict) -> object:
    return ToolUseBlock(id="t1", name=name, input=input)


def test_execute_runs_subagent_and_returns_final_text() -> None:
    fake = FakeModel(
        [
            [end_event(_tool_use("echo", {"text": "hi"}), stop_reason="tool_use")],
            [make_text_end("done")],
        ]
    )
    tool = AgentTool(model=fake, tools=(EchoTool(),), system="sub system")

    assert tool.execute({"task": "do X"}) == "done"
    # 子 agent 收到共享的 system、注入的工具集与含任务文本的首条 user 消息
    assert fake.calls[0]["system"] == "sub system"
    assert [t.name for t in fake.calls[0]["tools"]] == ["echo"]
    assert "do X" in fake.calls[0]["messages"][0].text


def test_result_is_last_assistant_message_text() -> None:
    fake = FakeModel(
        [
            [
                end_event(
                    TextBlock("narration"),
                    _tool_use("echo", {"text": "x"}),
                    stop_reason="tool_use",
                )
            ],
            [make_text_end("answer")],
        ]
    )
    tool = AgentTool(model=fake, tools=(EchoTool(),))

    # 只取最后一条 assistant 消息:中间轮次的叙述文本不进结果
    assert tool.execute({"task": "t"}) == "answer"


def test_empty_task_raises() -> None:
    tool = AgentTool(model=FakeModel([]), tools=())
    with pytest.raises(ValueError):
        tool.execute({"task": "   "})


def test_long_result_truncated() -> None:
    fake = FakeModel([[make_text_end("x" * 40_000)]])
    tool = AgentTool(model=fake, tools=())

    result = tool.execute({"task": "t"})

    assert result.endswith("(sub-agent output truncated)")
    assert len(result) < 40_000


def test_model_error_propagates() -> None:
    # 端到端场景由 wy-core 统一把异常转 Error: 的 tool_result
    tool = AgentTool(model=FakeModel([[ModelError("坏")]]), tools=())
    with pytest.raises(ModelError):
        tool.execute({"task": "t"})


def test_no_final_text_returns_placeholder() -> None:
    tool = AgentTool(model=FakeModel([[end_event()]]), tools=())
    assert "without a final text reply" in tool.execute({"task": "t"})


def test_each_spawn_writes_own_audit_file(tmp_path: Path) -> None:
    fake = FakeModel([[make_text_end("一")], [make_text_end("二")]])
    tool = AgentTool(model=fake, tools=(), audit_base=tmp_path / "sess")

    tool.execute({"task": "a"})
    tool.execute({"task": "b"})

    files = sorted(tmp_path.glob("sess.sub-*.audit.jsonl"))
    assert len(files) == 2  # 每次派生独立文件,并发不重名
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["kind"] == "agent_start"


def test_create_agent_wires_agent_tool(tmp_path: Path) -> None:
    fake = FakeModel(
        [
            [end_event(_tool_use("agent", {"task": "调研 packages 目录"}), stop_reason="tool_use")],
            [make_text_end("sub result")],
            [make_text_end("done")],
        ]
    )
    service = create_agent(
        model=fake, tools=(EchoTool(),), sessions_dir=tmp_path, audit=False
    )

    events = drain(service, "帮我调研")

    results = [e for e in events if isinstance(e, ToolResult)]
    assert results[0].content == "sub result"
    assert results[0].is_error is False
    parent_tools = [t.name for t in fake.calls[0]["tools"]]
    sub_tools = [t.name for t in fake.calls[1]["tools"]]
    assert "agent" in parent_tools
    assert sub_tools == ["echo"]  # 子 agent 工具集不含 agent:无嵌套派生
    service.close()


def test_create_agent_subagent_audit_next_to_parent(tmp_path: Path) -> None:
    fake = FakeModel(
        [
            [end_event(_tool_use("agent", {"task": "调研"}), stop_reason="tool_use")],
            [make_text_end("ok")],
            [make_text_end("done")],
        ]
    )
    service = create_agent(model=fake, tools=(), sessions_dir=tmp_path)

    drain(service, "去")

    sub_audits = list(tmp_path.glob(f"{service.session_id}.sub-*.audit.jsonl"))
    assert len(sub_audits) == 1  # 与主审计同目录,按会话基名命名
    service.close()


def test_subagent_gets_its_own_tool_search() -> None:
    fake = FakeModel(
        [
            [
                end_event(
                    _tool_use("tool_search", {"query": "select:lazy"}),
                    stop_reason="tool_use",
                )
            ],
            [make_text_end("sub result")],
        ]
    )
    tool = AgentTool(model=fake, tools=(EchoTool(), _LazyTool()))

    assert tool.execute({"task": "用懒加载工具"}) == "sub result"
    # 子 agent 自带 tool_search,懒加载工具首轮不发、加载后随下一轮发出
    assert [t.name for t in fake.calls[0]["tools"]] == ["echo", "tool_search"]
    assert [t.name for t in fake.calls[1]["tools"]] == ["echo", "lazy", "tool_search"]


def test_subagent_activation_does_not_leak_to_parent(tmp_path: Path) -> None:
    fake = FakeModel(
        [
            [end_event(_tool_use("agent", {"task": "调研"}), stop_reason="tool_use")],
            [
                end_event(
                    _tool_use("tool_search", {"query": "select:lazy"}),
                    stop_reason="tool_use",
                )
            ],
            [make_text_end("sub result")],
            [make_text_end("done")],
        ]
    )
    service = create_agent(
        model=fake, tools=(_LazyTool(),), sessions_dir=tmp_path, audit=False
    )

    drain(service, "去")

    # 子 agent 加载了 lazy,父 agent 的工具列表不受影响
    parent_first = [t.name for t in fake.calls[0]["tools"]]
    parent_last = [t.name for t in fake.calls[3]["tools"]]
    assert "lazy" not in parent_first and "lazy" not in parent_last
    assert "lazy" in [t.name for t in fake.calls[2]["tools"]]  # 子 agent 第二轮
    assert not service._agent.toolset.is_active("lazy")
    service.close()
