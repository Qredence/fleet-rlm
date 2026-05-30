from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.env import write_env_file


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"DAYTONA_API_URL": "https://api.daytona.example"}, "DAYTONA_API_KEY"),
        ({"DAYTONA_API_KEY": "key"}, "DAYTONA_API_URL"),
    ],
)
def test_resolve_daytona_config_validation_failures(
    values: dict[str, str],
    message: str,
    clean_runtime_env: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.integrations.daytona.config import DaytonaConfigError, resolve_daytona_config

    with pytest.raises(DaytonaConfigError, match=message):
        resolve_daytona_config(values)


def test_resolve_daytona_config_prefers_local_env_file(
    clean_runtime_env: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from fleet_rlm.integrations.daytona.config import resolve_daytona_config

    (tmp_path / "pyproject.toml").write_text("[project]\nname='fleet-rlm'\n", encoding="utf-8")
    write_env_file(
        tmp_path,
        values={
            "DAYTONA_API_KEY": "file-key",
            "DAYTONA_API_URL": "https://file.daytona.example",
            "DAYTONA_TARGET": "eu-west",
        },
    )
    clean_runtime_env.setenv("DAYTONA_API_KEY", "env-key")
    clean_runtime_env.setenv("DAYTONA_API_URL", "https://env.daytona.example")
    clean_runtime_env.chdir(tmp_path)

    config = resolve_daytona_config()

    assert config.api_key == "file-key"
    assert config.api_url == "https://file.daytona.example"
    assert config.target == "eu-west"


def test_resolve_daytona_config_prefers_process_env_outside_local_mode(
    clean_runtime_env: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from fleet_rlm.integrations.daytona.config import resolve_daytona_config

    (tmp_path / "pyproject.toml").write_text("[project]\nname='fleet-rlm'\n", encoding="utf-8")
    write_env_file(
        tmp_path,
        values={
            "DAYTONA_API_KEY": "file-key",
            "DAYTONA_API_URL": "https://file.daytona.example",
        },
    )
    clean_runtime_env.setenv("APP_ENV", "production")
    clean_runtime_env.setenv("DAYTONA_API_KEY", "env-key")
    clean_runtime_env.setenv("DAYTONA_API_URL", "https://env.daytona.example")
    clean_runtime_env.chdir(tmp_path)

    config = resolve_daytona_config()

    assert config.api_key == "env-key"
    assert config.api_url == "https://env.daytona.example"


def test_resolve_daytona_lm_runtime_config_uses_small_model_contract(
    clean_runtime_env: pytest.MonkeyPatch,
) -> None:
    from fleet_rlm.integrations.daytona.config import resolve_daytona_lm_runtime_config

    config = resolve_daytona_lm_runtime_config(
        {
            "DSPY_LM_MODEL": "openai/gpt-4.1",
            "DSPY_LLM_API_KEY": "planner-key",
            "DSPY_LM_API_BASE": "https://litellm.example",
            "DSPY_LM_SMALL_MODEL": "openai/gpt-4.1-mini",
        }
    )

    assert config.model == "openai/gpt-4.1"
    assert config.api_key == "planner-key"
    assert config.api_base == "https://litellm.example"
    assert config.delegate_model == "openai/gpt-4.1-mini"
    assert config.delegate_api_key == "planner-key"
    assert config.delegate_api_base == "https://litellm.example"
