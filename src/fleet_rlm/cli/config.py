"""Shared CLI configuration parsing helpers."""

from __future__ import annotations

from collections.abc import Sequence

from omegaconf import OmegaConf
import typer

from fleet_rlm.integrations.config.env import AppConfig


_CURRENT_APP_CONFIG: AppConfig | None = None


def split_hydra_overrides(tokens: Sequence[str]) -> tuple[list[str], list[str]]:
    hydra_overrides: list[str] = []
    cli_args: list[str] = []
    for token in tokens:
        if "=" in token and not token.startswith("-"):
            hydra_overrides.append(token)
        else:
            cli_args.append(token)
    return hydra_overrides, cli_args


def set_current_app_config(config: AppConfig | None) -> None:
    """Store the active CLI config for command callbacks."""
    global _CURRENT_APP_CONFIG
    _CURRENT_APP_CONFIG = config


def get_current_app_config() -> AppConfig | None:
    """Return the active CLI config, if one has been initialized."""
    return _CURRENT_APP_CONFIG


def require_current_app_config(*, error_message: str | None = None) -> AppConfig:
    """Return the active CLI config or exit if command state is uninitialized."""
    config = get_current_app_config()
    if config is not None:
        return config

    if error_message:
        typer.echo(error_message, err=True)
    raise typer.Exit(code=1)


def initialize_app_config(overrides: list[str] | None = None) -> AppConfig:
    from hydra import compose, initialize_config_module

    with initialize_config_module(
        config_module="fleet_rlm.integrations.config", version_base=None
    ):
        cfg = compose(config_name="config", overrides=overrides or [])
        cfg_dict = OmegaConf.to_container(cfg, resolve=True)
        if not isinstance(cfg_dict, dict):
            raise ValueError("Hydra config must resolve to a mapping")
        normalized_cfg = {str(k): v for k, v in cfg_dict.items()}
        return AppConfig(**normalized_cfg)
