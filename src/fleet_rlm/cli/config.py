"""Shared CLI configuration parsing helpers."""

from __future__ import annotations

from collections.abc import Sequence

import typer

from fleet_rlm.integrations.config.process import ProcessConfig, load_process_config

_CURRENT_APP_CONFIG: ProcessConfig | None = None
_CURRENT_CONFIG_OVERRIDES: tuple[str, ...] = ()


def split_config_overrides(tokens: Sequence[str]) -> tuple[list[str], list[str]]:
    overrides: list[str] = []
    cli_args: list[str] = []
    for token in tokens:
        if "=" in token and not token.startswith("-"):
            overrides.append(token)
        else:
            cli_args.append(token)
    return overrides, cli_args


def set_current_app_config(config: ProcessConfig | None) -> None:
    global _CURRENT_APP_CONFIG
    _CURRENT_APP_CONFIG = config


def get_current_config_overrides() -> tuple[str, ...]:
    return _CURRENT_CONFIG_OVERRIDES


def get_current_app_config() -> ProcessConfig | None:
    return _CURRENT_APP_CONFIG


def require_current_app_config(*, error_message: str | None = None) -> ProcessConfig:
    config = get_current_app_config()
    if config is not None:
        return config
    if error_message:
        typer.echo(error_message, err=True)
    raise typer.Exit(code=1)


def initialize_app_config(overrides: list[str] | None = None) -> ProcessConfig:
    global _CURRENT_CONFIG_OVERRIDES
    _CURRENT_CONFIG_OVERRIDES = tuple(overrides or ())
    return load_process_config(overrides=_CURRENT_CONFIG_OVERRIDES).config


__all__ = [
    "get_current_app_config",
    "get_current_config_overrides",
    "initialize_app_config",
    "require_current_app_config",
    "set_current_app_config",
    "split_config_overrides",
]
