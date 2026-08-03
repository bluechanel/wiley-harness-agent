"""Agent 状态容器:Session + 命名扩展 + 生命周期钩子与快照契约。

分层:内存状态是权威,持久化是它的投影——由使用方实现(如把聚合
快照追加进会话 JSONL),恢复 = 回灌快照。核心不理解任何具体状态
语义:扩展是什么、快照里存什么、何时持久化,全部由使用方决定。
"""

from __future__ import annotations

from collections.abc import Sequence

from wy_core.session import Session


class StateExtension:
    """命名状态扩展:继承并覆写所需方法,默认实现全部为空操作。

    - ``key`` 类属性是扩展唯一名,聚合快照与恢复按它分发。
    - ``snapshot()`` 返回可 JSON 序列化的字典;返回 None 表示本扩展
      不持久化(易失状态,恢复后回到初始值,如文件读取缓存)。
    - ``restore(data)`` 从快照恢复,data 来自历史 ``snapshot()``;
      实现方自行容忍字段缺失(前向兼容)。
    - 生命周期钩子由 Agent 经 ``AgentState`` 在回合各阶段调用;
      钩子不得抛异常——抛出会进入 Agent 的统一异常回滚路径。
    """

    key: str

    def snapshot(self) -> dict | None:
        return None

    def restore(self, data: dict) -> None:
        pass

    def on_turn_start(self) -> None:
        pass

    def on_turn_end(self) -> None:
        pass

    def on_rollback(self) -> None:
        pass

    def on_compaction(self, dropped: int) -> None:
        pass


class AgentState:
    """聚合 Session 与命名扩展,是 Agent 全部可变状态的唯一容器。

    Agent 在回合生命周期各点调用 ``turn_start``(回合入口)、
    ``compaction``(压缩后)、``turn_end``(TurnEnd 前)与
    ``rollback``(异常回滚后),逐个分发给扩展的对应钩子。
    """

    def __init__(
        self,
        *,
        session: Session | None = None,
        extensions: Sequence[StateExtension] = (),
    ) -> None:
        self.session = session if session is not None else Session()
        self.extensions: dict[str, StateExtension] = {}
        for extension in extensions:
            if extension.key in self.extensions:
                raise ValueError(f"状态扩展 key 重复: {extension.key}")
            self.extensions[extension.key] = extension

    def get(self, key: str) -> StateExtension | None:
        return self.extensions.get(key)

    def snapshot(self) -> dict:
        """聚合各扩展快照为 ``{key: data}``,跳过不持久化(None)的扩展。"""
        result: dict = {}
        for key, extension in self.extensions.items():
            data = extension.snapshot()
            if data is not None:
                result[key] = data
        return result

    def restore(self, data: dict) -> None:
        """把聚合快照按 key 分发给对应扩展;未知 key 忽略(前向兼容)。"""
        for key, extension in self.extensions.items():
            if key in data:
                extension.restore(data[key])

    def turn_start(self) -> None:
        for extension in self.extensions.values():
            extension.on_turn_start()

    def turn_end(self) -> None:
        for extension in self.extensions.values():
            extension.on_turn_end()

    def rollback(self) -> None:
        for extension in self.extensions.values():
            extension.on_rollback()

    def compaction(self, dropped: int) -> None:
        for extension in self.extensions.values():
            extension.on_compaction(dropped)
