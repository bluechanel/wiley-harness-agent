"""factory:create_agent 的组装、恢复与审计接线(bootstrap 仅差 config 解析)。"""

from pathlib import Path

from wy_core import Usage

from wy_coding_agent import create_agent

from app_helpers import FakeModel, drain, make_text_end


def test_create_agent_runs_and_audits(tmp_path: Path) -> None:
    model = FakeModel(
        [[make_text_end("答", usage=Usage(input_tokens=7, output_tokens=3))]]
    )
    service = create_agent(
        model=model,
        tools=(),
        sessions_dir=tmp_path,
        instruction="你是测试助手",
    )

    drain(service, "问")

    session_file = tmp_path / f"{service.session_id}.jsonl"
    audit_file = tmp_path / f"{service.session_id}.audit.jsonl"
    assert session_file.is_file()
    assert audit_file.is_file()  # 审计默认开启,与会话文件同目录
    assert "你是测试助手" in (model.calls[0]["system"] or "")

    service.close()


def test_create_agent_restores_history_and_usage(tmp_path: Path) -> None:
    first = FakeModel(
        [[make_text_end("答一", usage=Usage(input_tokens=7, output_tokens=3))]]
    )
    service = create_agent(model=first, tools=(), sessions_dir=tmp_path)
    drain(service, "问一")
    session_id = service.session_id
    service.close()

    second = FakeModel([[make_text_end("答二")]])
    restored = create_agent(session_id, model=second, tools=(), sessions_dir=tmp_path)

    assert restored.total_usage == Usage(input_tokens=7, output_tokens=3)
    assert restored.last_context_tokens == 10
    assert [m.text for m in restored._agent.session.messages] == ["问一", "答一"]

    drain(restored, "问二")
    # 第二次请求包含恢复的问答对与新输入
    texts = [m.text for m in second.calls[0]["messages"]]
    assert texts == ["问一", "答一", "问二"]
    restored.close()


def test_create_agent_can_disable_audit(tmp_path: Path) -> None:
    service = create_agent(
        model=FakeModel([[make_text_end("好")]]),
        tools=(),
        sessions_dir=tmp_path,
        audit=False,
    )
    drain(service)
    assert not (tmp_path / f"{service.session_id}.audit.jsonl").exists()
    service.close()
