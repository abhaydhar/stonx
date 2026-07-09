"""
Configuration smoke tests.

These tests protect the non-agent workflow: core modules and unit tests should
not require LLM credentials just to construct ScannerConfig.
"""

import sys
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import ScannerConfig


def test_config_loads_without_anthropic_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    config = ScannerConfig(_env_file=None)

    assert config.ANTHROPIC_API_KEY is None
    assert config.CAPITAL == 1_000_000


def test_config_ignores_unrelated_dotenv_keys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "UNRELATED_FLAG=true\n"
        "RISK_PCT=0.02\n",
        encoding="utf-8",
    )

    config = ScannerConfig()

    assert config.RISK_PCT == 0.02
    assert config.ANTHROPIC_API_KEY is None
