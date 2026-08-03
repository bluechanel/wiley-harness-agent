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


def test_create_agent_wires_plan_mode(tmp_path: Path) -> None:
    service = create_agent(
        model=FakeModel([]), tools=(), sessions_dir=tmp_path, audit=False
    )

    tools = service._agent.tools
    assert "exit_plan_mode" in tools
    # 子 agent 的工具集快照不含 exit_plan_mode:plan 模式属主会话状态
    assert "exit_plan_mode" not in {t.name for t in tools["agent"]._tools}
    # exit 工具与 service 共享同一份状态
    assert service.plan_mode is not None
    service.plan_mode.enable()
    tools["exit_plan_mode"].execute({"plan": "x"})
    assert not service.plan_mode.active
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


def test_create_agent_wires_skills(tmp_path: Path) -> None:
    skill_dir = tmp_path / "my-skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: Ship the app to production.\n---\nRun deploy steps.",
        encoding="utf-8",
    )
    model = FakeModel([[make_text_end("答")]])
    service = create_agent(
        model=model,
        tools=(),
        sessions_dir=tmp_path / "sessions",
        skills_dirs=(tmp_path / "my-skills",),
        workspace=tmp_path,
        audit=False,
    )
    drain(service)

    assert [tool.name for tool in model.calls[0]["tools"]] == [
        "skill",
        "agent",
        "exit_plan_mode",
    ]
    system = model.calls[0]["system"] or ""
    assert "# Skills" in system
    assert "- deploy: Ship the app to production." in system
    service.close()


def test_create_agent_without_skills_dirs_has_no_skill_tool(tmp_path: Path) -> None:
    model = FakeModel([[make_text_end("答")]])
    service = create_agent(
        model=model,
        tools=(),
        sessions_dir=tmp_path,
        workspace=tmp_path,
        audit=False,
    )
    drain(service)
    # skills_dirs=None 即不启用 skill;agent 工具总是装配
    assert [tool.name for tool in model.calls[0]["tools"]] == ["agent", "exit_plan_mode"]
    assert "# Skills" not in (model.calls[0]["system"] or "")
    service.close()
