"""Tests for the config.toml parsing (non-model sections)."""

from pathlib import Path

import pytest

from wy_coding_agent import CompactionConfig, ConfigError, load_compaction_config
from wy_coding_agent.config import load_config


def test_load_compaction_config_reads_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "[compaction]\nmax_context_tokens = 50000\nkeep_recent = 4\n",
        encoding="utf-8",
    )
    assert load_compaction_config(config_path) == CompactionConfig(
        max_context_tokens=50000, keep_recent=4
    )


def test_load_compaction_config_defaults_when_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[anthropic]\napi_key = "k"\n', encoding="utf-8")
    assert load_compaction_config(config_path) == CompactionConfig()
    assert load_compaction_config(tmp_path / "missing.toml") == CompactionConfig()


@pytest.mark.parametrize(
    "body",
    [
        "[compaction]\nmax_context_tokens = 0\n",
        "[compaction]\nmax_context_tokens = \"many\"\n",
        "[compaction]\nkeep_recent = 0\n",
    ],
)
def test_load_compaction_config_rejects_invalid(tmp_path: Path, body: str) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_compaction_config(config_path)


def test_load_config_requires_anthropic_section(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[compaction]\nkeep_recent = 2\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(config_path)
