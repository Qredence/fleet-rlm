from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest


def test_build_parser_accepts_web_subcommand() -> None:
    from fleet_rlm.cli.main import _build_parser

    args = _build_parser().parse_args(["web", "--trace-mode", "verbose"])

    assert args.command == "web"
    assert args.trace_mode == "verbose"


def test_main_help_exits_with_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fleet_rlm.cli import main as main_module

    monkeypatch.setattr(main_module.sys, "argv", ["fleet", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        main_module.main()

    assert exc_info.value.code == 0
    assert "fleet interactive chat" in capsys.readouterr().out.lower()


def test_main_parses_args_and_runs_terminal_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.cli import main as main_module
    from fleet_rlm.integrations.config.env import AppConfig

    captured: dict[str, object] = {}

    def fake_initialize_app_config(overrides: list[str]) -> AppConfig:
        captured["overrides"] = list(overrides)
        return AppConfig()

    def fake_run_terminal_chat(*, config: AppConfig, options: object) -> None:
        captured["config"] = config
        captured["options"] = options

    monkeypatch.setattr(main_module, "initialize_app_config", fake_initialize_app_config)
    monkeypatch.setattr(main_module, "run_terminal_chat", fake_run_terminal_chat)
    monkeypatch.setattr(
        main_module.sys,
        "argv",
        [
            "fleet",
            "--docs-path",
            "README.md",
            "--trace-mode",
            "verbose",
            "--volume-name",
            "volume-a",
        ],
    )

    main_module.main()

    options = captured["options"]
    assert captured["overrides"] == []
    assert isinstance(captured["config"], AppConfig)
    assert options.docs_path == Path("README.md")
    assert options.trace_mode == "verbose"
    assert options.volume_name == "volume-a"


def test_run_web_ui_rewrites_argv_to_serve_api(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.cli import main as main_module

    forwarded: dict[str, list[str]] = {}
    for dependency in ("fastapi", "jwt", "uvicorn"):
        monkeypatch.setitem(sys.modules, dependency, ModuleType(dependency))

    def fake_cli_main() -> None:
        forwarded["argv"] = list(main_module.sys.argv)

    monkeypatch.setattr("fleet_rlm.cli.fleet_cli.main", fake_cli_main)
    monkeypatch.setattr(
        main_module.sys,
        "argv",
        ["fleet", "web", "agent.model=openai/test-model", "--trace-mode", "verbose"],
    )

    main_module._run_web_ui()

    assert forwarded["argv"] == [
        "fleet-rlm",
        "serve-api",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "agent.model=openai/test-model",
    ]
