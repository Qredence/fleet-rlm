from __future__ import annotations

import importlib.util
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


def _load_validate_env_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "validate_env.py"
    spec = importlib.util.spec_from_file_location("validate_env", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_print_masked_hides_secret_value() -> None:
    module = _load_validate_env_module()
    buffer = StringIO()

    with redirect_stdout(buffer):
        module._print_masked("DAYTONA_API_KEY", "supersecret-value")

    output = buffer.getvalue()
    assert "supersecret-value" not in output
    assert "supersecr" not in output
    assert "DAYTONA_API_KEY: ✓ (hidden)" in output
