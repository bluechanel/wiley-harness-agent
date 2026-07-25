from wiley_agent.tools import DEFAULT_TOOLS, Tool
from wiley_agent.tools.bash import BASH
from wiley_agent.tools.text_editor import TEXT_EDITOR


def test_default_tools_loads_every_tool_in_the_package() -> None:
    by_name = {tool.name: tool for tool in DEFAULT_TOOLS}

    assert by_name["bash"] is BASH
    assert by_name["text_editor"] is TEXT_EDITOR
    assert len(DEFAULT_TOOLS) == 2


def test_default_tools_are_tool_instances_with_unique_names() -> None:
    names = [tool.name for tool in DEFAULT_TOOLS]

    assert all(isinstance(tool, Tool) for tool in DEFAULT_TOOLS)
    assert len(names) == len(set(names))
