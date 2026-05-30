from __future__ import annotations

import importlib.util
from pathlib import Path


def load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "diagnose.py"
    spec = importlib.util.spec_from_file_location("diagnose", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnose = load_script()


def test_check_env_accepts_dspy_lm_key(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("DSPY_LM_MODEL=test\n", encoding="utf-8")
    monkeypatch.setenv("DSPY_LM_MODEL", "test-model")
    monkeypatch.delenv("DSPY_LLM_API_KEY", raising=False)
    monkeypatch.setenv("DSPY_LM_API_KEY", "secret")

    assert diagnose.check_env() is True


def test_check_daytona_reports_missing_required_env(monkeypatch) -> None:
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    monkeypatch.delenv("DAYTONA_API_URL", raising=False)

    assert diagnose.check_daytona() is False
