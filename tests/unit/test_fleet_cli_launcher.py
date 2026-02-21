from __future__ import annotations

import pytest

from fleet_rlm import fleet_cli
from fleet_rlm.config import AppConfig


def test_main_uses_python_ui(monkeypatch: pytest.MonkeyPatch) -> None:
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
