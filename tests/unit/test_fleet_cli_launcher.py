from __future__ import annotations

import pytest

import fleet_rlm.cli.main as fleet_cli
from fleet_rlm.integrations.config.env import AppConfig


def test_main_uses_python_ui(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fleet_cli, "initialize_app_config", lambda _overrides: AppConfig()
    )
    called: dict[str, object] = {}

    def fake_run_terminal_chat(*, config: AppConfig, options: object) -> None:
        called["config"] = config
        called["options"] = options

    monkeypatch.setattr(fleet_cli, "run_terminal_chat", fake_run_terminal_chat)
    monkeypatch.setattr(fleet_cli.sys, "argv", ["fleet", "--trace-mode", "verbose"])

    fleet_cli.main()

    assert isinstance(called["config"], AppConfig)
    assert getattr(called["options"], "trace_mode") == "verbose"


def test_web_subcommand_rewrites_to_serve_api_on_port_8000(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    forwarded: dict[str, list[str]] = {}

    def fake_cli_main() -> None:
        forwarded["argv"] = list(fleet_cli.sys.argv)

    monkeypatch.setattr("fleet_rlm.cli.fleet_cli.main", fake_cli_main)
    monkeypatch.setattr(
        fleet_cli.sys,
        "argv",
        ["fleet", "web", "runtime_mode=daytona_pilot"],
    )

    fleet_cli.main()

    assert forwarded["argv"] == [
        "fleet-rlm",
        "serve-api",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "runtime_mode=daytona_pilot",
    ]
    assert (
        "Starting Web UI and API server on http://0.0.0.0:8000 ..."
        in capsys.readouterr().out
    )
