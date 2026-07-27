from wy_core import Tool

from wy_coding_agent.tools import DEFAULT_TOOLS
from wy_coding_agent.tools.bash import BASH
from wy_coding_agent.tools.edit import EDIT
from wy_coding_agent.tools.grep import GREP
from wy_coding_agent.tools.read import READ
from wy_coding_agent.tools.write import WRITE


def test_default_tools_loads_every_tool_in_the_package() -> None:
    by_name = {tool.name: tool for tool in DEFAULT_TOOLS}

    assert by_name["bash"] is BASH
    assert by_name["grep"] is GREP
    assert by_name["read"] is READ
    assert by_name["edit"] is EDIT
    assert by_name["write"] is WRITE
    assert len(DEFAULT_TOOLS) == 5


def test_default_tools_are_tool_instances_with_unique_names() -> None:
    names = [tool.name for tool in DEFAULT_TOOLS]

    assert all(isinstance(tool, Tool) for tool in DEFAULT_TOOLS)
    assert len(names) == len(set(names))
    assert all(tool.description for tool in DEFAULT_TOOLS)
    assert all(tool.parameters.get("type") == "object" for tool in DEFAULT_TOOLS)
