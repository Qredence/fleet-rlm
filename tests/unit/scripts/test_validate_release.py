"""Unit coverage for release wheel validation helpers."""

from __future__ import annotations

import importlib.util
from argparse import Namespace
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "validate_release.py"
SPEC = importlib.util.spec_from_file_location("fleet_rlm_validate_release", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
validate_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate_release)


@pytest.mark.unit
def test_has_ui_entrypoint_accepts_root_or_client_index() -> None:
    """A valid packaged UI may expose the HTML entrypoint at root or under client/."""
    assert validate_release._has_ui_entrypoint({"index.html"})
    assert validate_release._has_ui_entrypoint({"client/index.html"})
    assert not validate_release._has_ui_entrypoint({"assets/app.js"})


@pytest.mark.unit
def test_do_wheel_fails_when_frontend_dist_has_no_html_entrypoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Wheel validation rejects clean builds that cannot serve HTML."""
    wheel_path = tmp_path / "fleet_rlm-0.5.3-py3-none-any.whl"
    wheel_path.write_bytes(b"placeholder")

    monkeypatch.setattr(validate_release, "_collect_local_frontend", lambda _path: {"assets/app.js": "local-hash"})
    monkeypatch.setattr(
        validate_release, "_collect_wheel_frontend", lambda _path: ({"assets/app.js": "wheel-hash"}, [])
    )

    result = validate_release.do_wheel(
        Namespace(
            wheel=wheel_path,
            dist_dir=tmp_path,
            frontend_dist=tmp_path / "frontend-dist",
        )
    )

    captured = capsys.readouterr()
    assert result == 1
    assert "Frontend dist is missing a served HTML entrypoint" in captured.err
