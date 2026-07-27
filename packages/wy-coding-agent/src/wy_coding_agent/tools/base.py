"""工具基座:re-export wy-core 的 Tool 契约,并提供函数式适配器。"""

from collections.abc import Callable, Mapping
from typing import Any

from wy_core import Tool

__all__ = ["FunctionTool", "Tool"]


class FunctionTool(Tool):
    """由 definition 字典(name/description/input_schema)加执行函数构成的工具。

    便于沿用"模块级 schema 字典 + 执行函数"的内置工具写法,以及包装 MCP
    等运行期发现的工具;静态工具直接继承 ``wy_core.Tool`` 亦可。
    """

    def __init__(
        self,
        definition: Mapping[str, Any],
        execute: Callable[[Mapping[str, Any]], str],
    ) -> None:
        self.name = str(definition["name"])
        self.description = str(definition.get("description", ""))
        self.parameters = dict(definition.get("input_schema", {}))
        self._execute = execute

    def execute(self, input: dict) -> str:
        return self._execute(input)
