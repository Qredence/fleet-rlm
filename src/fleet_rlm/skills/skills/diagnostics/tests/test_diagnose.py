from __future__ import annotations

import importlib.util
from pathlib import Path


def load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "diagnose.py"
    spec = importlib.util.spec_from_file_location("diagnose_clean", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


diagnose = load_script()


def test_check_env_accepts_local_scope(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    Path(".env").write_text("FLEET_RUN_ENVIRONMENT=deno\n", encoding="utf-8")
    monkeypatch.delenv("FLEET_LLM_API_KEY", raising=False)
    monkeypatch.delenv("FLEET_DAYTONA_API_KEY", raising=False)

    assert diagnose.check_env() is True


def test_check_package_reports_importable() -> None:
    assert diagnose.check_package() is True


def test_secret_presence_never_echoes_value(capsys) -> None:
    diagnose.secret_presence("FLEET_LLM_API_KEY", "super-secret-value")
    out = capsys.readouterr().out
    assert "super-secret-value" not in out
    assert "SET" in out
