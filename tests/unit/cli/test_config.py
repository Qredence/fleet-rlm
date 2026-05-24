from __future__ import annotations

import sys
from collections.abc import Generator
from types import ModuleType

import pytest
import typer


@pytest.fixture(autouse=True)
def _reset_current_app_config() -> Generator[None, None, None]:
    from fleet_rlm.cli.config import set_current_app_config

    set_current_app_config(None)
    yield
    set_current_app_config(None)


def test_split_hydra_overrides_separates_key_value_tokens() -> None:
    from fleet_rlm.cli.config import split_hydra_overrides

    hydra_overrides, cli_args = split_hydra_overrides(
        [
            "agent.model=openai/gpt-4o-mini",
            "--trace-mode",
            "verbose",
            "sandbox.timeout=42",
            "--port=8000",
        ]
    )

    assert hydra_overrides == ["agent.model=openai/gpt-4o-mini", "sandbox.timeout=42"]
    assert cli_args == ["--trace-mode", "verbose", "--port=8000"]


def test_initialize_and_get_current_app_config_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    clean_runtime_env: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.cli import config as config_module

    class _HydraContext:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_hydra = ModuleType("hydra")
    compose_calls: dict[str, object] = {}

    def fake_compose(*, config_name: str, overrides: list[str]) -> object:
        compose_calls["config_name"] = config_name
        compose_calls["overrides"] = list(overrides)
        return object()

    fake_hydra.compose = fake_compose  # ty: ignore[unresolved-attribute]
    fake_hydra.initialize_config_module = lambda **_: _HydraContext()  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "hydra", fake_hydra)
    monkeypatch.setattr(
        config_module.OmegaConf,
        "to_container",
        lambda cfg, resolve: {
            "agent": {"model": "openai/test-model"},
            "interpreter": {"timeout": 123},
        },
    )

    config = config_module.initialize_app_config(["agent.model=openai/test-model"])
    config_module.set_current_app_config(config)

    assert compose_calls == {
        "config_name": "config",
        "overrides": ["agent.model=openai/test-model"],
    }
    assert config.agent.model == "openai/test-model"
    assert config.interpreter.timeout == 123
    assert config_module.get_current_app_config() is config
    assert config_module.require_current_app_config() is config


def test_require_current_app_config_exits_when_missing(capsys: pytest.CaptureFixture[str]) -> None:
    from fleet_rlm.cli.config import require_current_app_config

    with pytest.raises(typer.Exit) as exc_info:
        require_current_app_config(error_message="config missing")

    assert exc_info.value.exit_code == 1
    assert "config missing" in capsys.readouterr().err
