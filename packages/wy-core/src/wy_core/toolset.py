"""工具集合:直接加载工具恒在列,懒加载工具经 activate 按需入列。

``Tool.deferred`` 区分两类工具:False(默认)即直接加载,每次请求都发给
模型;True 即懒加载,不进默认工具列表,由使用方(如工具搜索工具)调
``activate`` 后才随请求发送。``Agent`` 每轮请求只发 ``available``,
``all`` 始终是全量(执行时按名字查表,已调用的工具不因未激活而失联)。

激活状态是内存态:核心不做持久化,会话恢复后回到初始集合。
"""

from __future__ import annotations

from collections.abc import Sequence

from wy_core.tool import Tool


class ToolSet:
    """工具注册表 + 懒加载工具的激活状态。"""

    def __init__(self, tools: Sequence[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        self._activated: set[str] = set()
        for tool in tools:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        """注册一个工具;重名抛 ValueError。"""
        if tool.name in self._tools:
            raise ValueError("工具名重复")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def all(self) -> tuple[Tool, ...]:
        """全部工具,注册顺序。"""
        return tuple(self._tools.values())

    @property
    def deferred(self) -> tuple[Tool, ...]:
        """懒加载且尚未激活的工具。"""
        return tuple(
            tool
            for name, tool in self._tools.items()
            if tool.deferred and name not in self._activated
        )

    @property
    def available(self) -> tuple[Tool, ...]:
        """本轮请求应发给模型的工具:直接加载 + 已激活,顺序同 ``all``。"""
        return tuple(
            tool
            for name, tool in self._tools.items()
            if not tool.deferred or name in self._activated
        )

    def activate(self, *names: str) -> tuple[str, ...]:
        """加载懒加载工具,返回本次真正新激活的名字。

        未知名与非懒加载(本就在列)的名字一律忽略,重复激活是幂等的——
        调用方可以直接把模型给的名字传进来,不必先行过滤。
        """
        activated: list[str] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None or not tool.deferred or name in self._activated:
                continue
            self._activated.add(name)
            activated.append(name)
        return tuple(activated)

    def is_active(self, name: str) -> bool:
        """该工具当前是否随请求发送(直接加载或已激活)。"""
        tool = self._tools.get(name)
        return tool is not None and (not tool.deferred or name in self._activated)

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self):
        return iter(self._tools.values())

    def __contains__(self, name: object) -> bool:
        return name in self._tools
