"""内置工具与自动发现。

每个子模块直接继承 ``wy_core.Tool`` 定义工具类(``name``/``description``/
``parameters`` 类属性 + ``execute`` 方法)并给出模块级实例;``DEFAULT_TOOLS``
自动扫描本包全部子模块收集这些实例——新增内置工具即新增子模块,无需维护
注册表。只有类而无模块级实例的模块(如 ``mcp_tool``,由 mcp 桥接层按连接
构造)与共享辅助模块(如 ``files``)可以并存,扫描自然跳过。外部工具按
``wy_core.Tool`` 契约编写后经 ``create_agent(tools=...)`` 注入。
"""

import importlib
import pkgutil

from wy_core import Tool


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

__all__ = ["DEFAULT_TOOLS", "Tool"]
