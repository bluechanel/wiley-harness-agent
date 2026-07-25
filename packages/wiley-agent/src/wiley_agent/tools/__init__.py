"""Agent base tools.

Every submodule in this package defines its tool as a module-level ``Tool``
instance; ``DEFAULT_TOOLS`` discovers and loads all of them automatically, so
adding a tool means adding a submodule — no registration list to maintain.
"""

import importlib
import pkgutil

from wiley_agent.tools.base import Tool


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
