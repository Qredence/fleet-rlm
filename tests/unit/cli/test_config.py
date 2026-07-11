from __future__ import annotations

from collections.abc import Generator

import pytest
import typer


@pytest.fixture(autouse=True)
def _reset_current_app_config() -> Generator[None, None, None]:
    from fleet_rlm.cli.config import set_current_app_config

    set_current_app_config(None)
    from fleet_rlm.cli.config import initialize_app_config

    initialize_app_config([])
    yield
    set_current_app_config(None)
    initialize_app_config([])


def test_split_config_overrides_separates_key_value_tokens() -> None:
    from fleet_rlm.cli.config import split_config_overrides

    config_overrides, cli_args = split_config_overrides(
        [
            "llm.roles.planner.model=openai/gpt-4o-mini",
            "--trace-mode",
            "verbose",
            "sandbox.timeout=42",
            "--port=8000",
        ]
    )

    assert config_overrides == ["llm.roles.planner.model=openai/gpt-4o-mini", "sandbox.timeout=42"]
    assert cli_args == ["--trace-mode", "verbose", "--port=8000"]


def test_initialize_and_get_current_app_config_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    clean_runtime_env: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.cli import config as config_module

    config = config_module.initialize_app_config(["llm.model=openai/test-model", "sandbox.timeout=123"])
    config_module.set_current_app_config(config)

    assert config.llm.roles.planner.model == "openai/test-model"
    assert config.daytona.execution_timeout_s == 123
    assert config_module.get_current_app_config() is config
    assert config_module.require_current_app_config() is config
    assert config_module.get_current_config_overrides() == (
        "llm.model=openai/test-model",
        "sandbox.timeout=123",
    )


def test_require_current_app_config_exits_when_missing(capsys: pytest.CaptureFixture[str]) -> None:
    from fleet_rlm.cli.config import require_current_app_config

    with pytest.raises(typer.Exit) as exc_info:
        require_current_app_config(error_message="config missing")

    assert exc_info.value.exit_code == 1
    assert "config missing" in capsys.readouterr().err


def test_serve_api_uses_canonical_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    from fleet_rlm.api import main as api_main
    from fleet_rlm.cli.commands.serve_cmds import serve_api_command
    from fleet_rlm.cli.config import initialize_app_config, set_current_app_config

    config = initialize_app_config(["api.host=127.0.0.9", "api.port=8123"])
    set_current_app_config(config)
    captured: dict[str, object] = {}
    monkeypatch.setattr(api_main, "create_app", lambda **_: object())
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda app, *, host, port: captured.update(app=app, host=host, port=port),
    )

    serve_api_command(host=None, port=None)

    assert captured["host"] == "127.0.0.9"
    assert captured["port"] == 8123
