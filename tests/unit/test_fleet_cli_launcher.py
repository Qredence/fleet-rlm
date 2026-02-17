from __future__ import annotations

from pathlib import Path

import pytest

from fleet_rlm import fleet_cli
from fleet_rlm.config import AppConfig


def test_main_falls_back_to_python_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fleet_cli, "_find_ink_cli", lambda: None)
    monkeypatch.setattr(fleet_cli, "_initialize_config", lambda _overrides: AppConfig())
    called: dict[str, object] = {}

    def fake_run_terminal_chat(*, config: AppConfig, options: object) -> None:
        called["config"] = config
        called["options"] = options

    monkeypatch.setattr(fleet_cli, "run_terminal_chat", fake_run_terminal_chat)
    monkeypatch.setattr(fleet_cli.sys, "argv", ["fleet", "--trace-mode", "verbose"])

    fleet_cli.main()

    assert isinstance(called["config"], AppConfig)
    assert getattr(called["options"], "trace_mode") == "verbose"


def test_main_ink_mode_errors_when_bundle_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fleet_cli, "_find_ink_cli", lambda: None)
    monkeypatch.setattr(fleet_cli.sys, "argv", ["fleet", "--ui", "ink"])

    with pytest.raises(SystemExit) as exc:
        fleet_cli.main()
    assert exc.value.code == 2


def test_main_uses_ink_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fleet_cli, "_find_ink_cli", lambda: Path("/tmp/cli.js"))
    monkeypatch.setattr(fleet_cli, "_run_ink_ui", lambda **_kwargs: 0)
    monkeypatch.setattr(
        fleet_cli,
        "_initialize_config",
        lambda _overrides: (_ for _ in ()).throw(RuntimeError("should not be called")),
    )
    monkeypatch.setattr(
        fleet_cli,
        "run_terminal_chat",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("should not run python UI")
        ),
    )
    monkeypatch.setattr(fleet_cli.sys, "argv", ["fleet"])

    fleet_cli.main()


def test_main_passes_explicit_volume_and_secret_to_ink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fleet_cli, "_find_ink_cli", lambda: Path("/tmp/cli.js"))
    captured: dict[str, object] = {}

    def fake_run_ink_ui(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(fleet_cli, "_run_ink_ui", fake_run_ink_ui)
    monkeypatch.setattr(
        fleet_cli,
        "_initialize_config",
        lambda _overrides: (_ for _ in ()).throw(RuntimeError("should not be called")),
    )
    monkeypatch.setattr(
        fleet_cli.sys,
        "argv",
        [
            "fleet",
            "--volume-name",
            "vol-123",
            "--secret-name",
            "SECRET_123",
        ],
    )

    fleet_cli.main()

    assert captured["volume_name"] == "vol-123"
    assert captured["secret_name"] == "SECRET_123"
