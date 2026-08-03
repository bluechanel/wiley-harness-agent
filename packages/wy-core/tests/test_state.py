"""state 模块:扩展分发、快照/恢复、Agent 生命周期钩子。"""

import pytest

from wy_core import Agent, AgentState, ModelError, Session, StateExtension

from helpers import FakeModel, make_text_end, run_events


class Recorder(StateExtension):
    """记录钩子调用顺序并携带可持久化数据的测试扩展。"""

    key = "rec"

    def __init__(self) -> None:
        self.events: list[str] = []
        self.data = {"n": 1}

    def snapshot(self) -> dict:
        return dict(self.data)

    def restore(self, data: dict) -> None:
        self.data = dict(data)
        self.events.append("restore")

    def on_turn_start(self) -> None:
        self.events.append("turn_start")

    def on_turn_end(self) -> None:
        self.events.append("turn_end")

    def on_rollback(self) -> None:
        self.events.append("rollback")

    def on_compaction(self, dropped: int) -> None:
        self.events.append(f"compaction:{dropped}")


class Volatile(StateExtension):
    """全默认实现:snapshot 为 None,不参与持久化。"""

    key = "vol"


def test_快照聚合跳过不持久化扩展():
    state = AgentState(extensions=(Recorder(), Volatile()))
    assert state.snapshot() == {"rec": {"n": 1}}


def test_恢复按key分发_未知key忽略():
    rec = Recorder()
    state = AgentState(extensions=(rec,))
    state.restore({"rec": {"n": 7}, "ghost": {"x": 1}})
    assert rec.data == {"n": 7}
    assert rec.events == ["restore"]


def test_扩展key重复拒绝():
    with pytest.raises(ValueError):
        AgentState(extensions=(Recorder(), Recorder()))


def test_session与state互斥():
    with pytest.raises(ValueError):
        Agent(
            model=FakeModel([]),
            session=Session(),
            state=AgentState(),
            audit=None,
        )


def test_session兼容别名():
    session = Session()
    agent = Agent(model=FakeModel([]), session=session, audit=None)
    assert agent.session is session
    assert agent.state.session is session

    state = AgentState()
    agent2 = Agent(model=FakeModel([]), state=state, audit=None)
    assert agent2.session is state.session


def test_回合生命周期钩子时序():
    rec = Recorder()
    agent = Agent(
        model=FakeModel([[make_text_end("好")]]),
        state=AgentState(extensions=(rec,)),
        audit=None,
    )
    run_events(agent, "hi")
    assert rec.events == ["turn_start", "turn_end"]


def test_异常触发rollback钩子():
    rec = Recorder()
    agent = Agent(
        model=FakeModel([[ModelError("挂了")]]),
        state=AgentState(extensions=(rec,)),
        audit=None,
    )
    with pytest.raises(ModelError):
        run_events(agent, "hi")
    assert rec.events == ["turn_start", "rollback"]
    assert agent.session.messages == []  # 消息回滚在钩子之前完成


def test_压缩触发compaction钩子():
    rec = Recorder()
    state = AgentState(
        session=Session(max_context_tokens=1, keep_recent=1),
        extensions=(rec,),
    )
    model = FakeModel(
        [
            [make_text_end("第一答")],
            [make_text_end("这是摘要")],  # 第二回合开头的压缩请求
            [make_text_end("第二答")],
        ]
    )
    agent = Agent(model=model, state=state, audit=None)
    run_events(agent, "第一问")
    run_events(agent, "第二问")
    assert "compaction:2" in rec.events
