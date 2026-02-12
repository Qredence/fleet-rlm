from __future__ import annotations

from pathlib import Path

import fleet_rlm.core.config as config


class _FakeLM:
    def __init__(self, model, api_base=None, api_key=None, max_tokens=None):
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.max_tokens = max_tokens


def test_configure_planner_from_env_with_quotes(monkeypatch, tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'DSPY_LM_MODEL="openai/test-model"\n'
        "DSPY_LLM_API_KEY='sk-test'\n"
        "DSPY_LM_API_BASE=https://example.test\n"
        "DSPY_LM_MAX_TOKENS=1234\n"
    )

    captured = {}

    def fake_configure(*, lm):
        captured["lm"] = lm

    monkeypatch.setattr(config.dspy, "LM", _FakeLM)
    monkeypatch.setattr(config.dspy, "configure", fake_configure)

    monkeypatch.delenv("DSPY_LM_MODEL", raising=False)
    monkeypatch.delenv("DSPY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DSPY_LM_API_KEY", raising=False)
    monkeypatch.delenv("DSPY_LM_API_BASE", raising=False)
    monkeypatch.delenv("DSPY_LM_MAX_TOKENS", raising=False)

    assert config.configure_planner_from_env(env_file=env_file) is True
    lm = captured["lm"]
    assert lm.model == "openai/test-model"
    assert lm.api_key == "sk-test"
    assert lm.api_base == "https://example.test"
    assert lm.max_tokens == 1234


def test_configure_planner_from_env_fallback_api_key(monkeypatch, tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("DSPY_LM_MODEL=openai/test\nDSPY_LM_API_KEY=sk-fallback\n")

    monkeypatch.setattr(config.dspy, "LM", _FakeLM)
    monkeypatch.setattr(config.dspy, "configure", lambda *, lm: None)

    monkeypatch.delenv("DSPY_LM_MODEL", raising=False)
    monkeypatch.delenv("DSPY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DSPY_LM_API_KEY", raising=False)

    assert config.configure_planner_from_env(env_file=env_file) is True


def test_configure_planner_from_env_missing_vars(monkeypatch, tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("")

    monkeypatch.delenv("DSPY_LM_MODEL", raising=False)
    monkeypatch.delenv("DSPY_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DSPY_LM_API_KEY", raising=False)

    assert config.configure_planner_from_env(env_file=env_file) is False
