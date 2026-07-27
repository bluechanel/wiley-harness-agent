"""内置工具与自动发现。

每个子模块以模块级 ``Tool`` 实例(通常经 ``FunctionTool`` 构造)定义一个
内置工具;``DEFAULT_TOOLS`` 自动扫描本包全部子模块收集它们——新增内置
工具即新增子模块,无需维护注册表。不含 ``Tool`` 实例的共享辅助模块
(如 ``file_state``)可以并存,扫描自然跳过。外部工具按 ``wy_core.Tool``
契约编写后经 ``create_agent(tools=...)`` 注入。
"""

import importlib
import pkgutil

from wy_coding_agent.tools.base import FunctionTool, Tool


def _discover_tools() -> tuple[Tool, ...]:
    """Collect every Tool instance defined in this package's submodules."""
    tools: dict[str, Tool] = {}
    for module_info in sorted(pkgutil.iter_modules(__path__), key=lambda info: info.name):
        module = importlib.import_module(f"{__name__}.{module_info.name}")
        for value in vars(module).values():
            if not isinstance(value, Tool):
                continue
            existing = tools.get(value.name)
            if existing is None:
                tools[value.name] = value
            elif existing is not value:
                raise ValueError(f"Duplicate tool name: {value.name!r}")
    return tuple(tools.values())


DEFAULT_TOOLS: tuple[Tool, ...] = _discover_tools()

__all__ = ["DEFAULT_TOOLS", "FunctionTool", "Tool"]
