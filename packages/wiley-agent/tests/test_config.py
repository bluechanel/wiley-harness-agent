"""Tests for the config.toml parsing (non-provider sections)."""

from pathlib import Path

import pytest

from wiley_agent import ConfigError, DebugConfig
from wiley_agent.config import load_debug_config


def test_load_debug_config_reads_enabled_flag(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[debug]\nenabled = true\n", encoding="utf-8")
    assert load_debug_config(config_path) == DebugConfig(enabled=True)


def test_load_debug_config_defaults_to_disabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[anthropic]\napi_key = "k"\n', encoding="utf-8")
    assert load_debug_config(config_path) == DebugConfig(enabled=False)
    assert load_debug_config(tmp_path / "missing.toml") == DebugConfig(enabled=False)


def test_load_debug_config_rejects_non_boolean(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[debug]\nenabled = "yes"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_debug_config(config_path)
