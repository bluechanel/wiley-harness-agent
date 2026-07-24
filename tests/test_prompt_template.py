from wiley_harness_agent.agent.prompt_template import build_system_prompt


def test_build_system_prompt_strips_instruction() -> None:
    assert build_system_prompt("  be brief  ") == "be brief"


def test_build_system_prompt_empty_returns_none() -> None:
    assert build_system_prompt(None) is None
    assert build_system_prompt("   ") is None
