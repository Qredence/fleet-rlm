from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.env import write_env_file


def _snapshot_fields(snapshot: dict[str, object]) -> dict[str, dict[str, object]]:
    categories = snapshot["categories"]
    assert isinstance(categories, list)
    return {field["key"]: field for category in categories for field in category["fields"]}  # ty: ignore[not-subscriptable]


def test_resolve_env_path_prefers_explicit_override(clean_runtime_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fleet_rlm.integrations.config.env_file import resolve_env_path

    explicit = tmp_path / "custom.env"
    clean_runtime_env.setenv("FLEET_RLM_ENV_PATH", str(explicit))

    assert resolve_env_path() == explicit.resolve()


def test_resolve_env_path_searches_upward_for_pyproject(clean_runtime_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fleet_rlm.integrations.config.env_file import resolve_env_path

    repo_root = tmp_path / "repo"
    nested = repo_root / "src" / "fleet_rlm"
    nested.mkdir(parents=True)
    (repo_root / "pyproject.toml").write_text("[project]\nname='fleet-rlm'\n", encoding="utf-8")
    probe = nested / "module.py"
    probe.write_text("# marker\n", encoding="utf-8")

    assert resolve_env_path(start_paths=[probe]) == repo_root / ".env"


def test_get_settings_snapshot_masks_secrets_and_preserves_categories(
    clean_runtime_env: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from fleet_rlm.integrations.config.env_file import get_settings_snapshot

    env_path = write_env_file(
        tmp_path,
        lines=[
            "DSPY_LM_MODEL=openai/file-model",
            "DSPY_LLM_API_KEY=sk-file-secret-12345",
        ],
    )
    clean_runtime_env.setenv("DSPY_LM_MODEL", "openai/env-model")
    clean_runtime_env.setenv("DSPY_LLM_API_KEY", "sk-env-secret-99999")

    snapshot = get_settings_snapshot(
        keys=["DSPY_LM_MODEL", "DSPY_LLM_API_KEY", "DAYTONA_TARGET"],
        extra_values={"DAYTONA_TARGET": "local"},
        env_path=env_path,
    )

    fields = _snapshot_fields(snapshot)
    assert [category["id"] for category in snapshot["categories"]] == ["llm", "api_keys", "sandbox_volumes"]
    assert snapshot["env_path"] == str(env_path)
    assert fields["DSPY_LM_MODEL"]["value"] == "openai/file-model"
    assert fields["DSPY_LLM_API_KEY"]["value"] == "sk-...45"
    assert fields["DSPY_LLM_API_KEY"]["masked_value"] == "sk-...45"
    assert fields["DSPY_LLM_API_KEY"]["secret"] is True
    assert fields["DAYTONA_TARGET"]["value"] == "local"


def test_apply_env_updates_ignores_masked_secret_round_trip(
    clean_runtime_env: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from fleet_rlm.integrations.config.env_file import apply_env_updates, mask_secret

    llm_secret = "supersecret66"
    daytona_secret = "daytonasecret99"
    env_path = write_env_file(
        tmp_path,
        lines=[
            f"DSPY_LLM_API_KEY={llm_secret}",
            f"DAYTONA_API_KEY={daytona_secret}",
            "DSPY_LM_MODEL=openai/gpt-4o-mini",
        ],
    )
    clean_runtime_env.setenv("DSPY_LLM_API_KEY", llm_secret)
    clean_runtime_env.setenv("DAYTONA_API_KEY", daytona_secret)

    result = apply_env_updates(
        updates={
            "DSPY_LLM_API_KEY": mask_secret(llm_secret),
            "DAYTONA_API_KEY": mask_secret(daytona_secret),
            "DSPY_LM_MODEL": "openai/gpt-4.1-mini",
        },
        env_path=env_path,
    )

    text = env_path.read_text(encoding="utf-8")
    assert result["updated"] == ["DSPY_LM_MODEL"]
    assert "DSPY_LLM_API_KEY=supersecret66" in text
    assert "DAYTONA_API_KEY=daytonasecret99" in text
    assert "DSPY_LM_MODEL='openai/gpt-4.1-mini'" in text


def test_process_config_validates_canonical_sections() -> None:
    from fleet_rlm.integrations.config.process import ProcessConfig

    config = ProcessConfig.model_validate(
        {
            "llm": {
                "roles": {
                    "planner": {"model": "openai/gpt-4.1"},
                    "delegate": {"model": "openai/gpt-4.1-mini", "max_tokens": 2048},
                }
            },
            "daytona": {
                "execution_timeout_s": 321,
                "volume_name": "tenant-volume",
            },
        }
    )

    assert config.llm.roles.planner.model == "openai/gpt-4.1"
    assert config.llm.roles.delegate.model == "openai/gpt-4.1-mini"
    assert config.llm.roles.delegate.max_tokens == 2048
    assert config.daytona.execution_timeout_s == 321
    assert config.daytona.volume_name == "tenant-volume"


def test_initialize_app_config_loads_pruned_default_config(clean_runtime_env: pytest.MonkeyPatch) -> None:
    from fleet_rlm.cli.config import initialize_app_config

    clean_runtime_env.setenv("DSPY_LM_MODEL", "openai/pruned-default")
    clean_runtime_env.setenv("VOLUME_NAME", "rlm-volume-test")

    config = initialize_app_config()

    assert config.llm.roles.planner.model == "openai/pruned-default"
    assert config.daytona.volume_name == "rlm-volume-test"
    assert config.persistence.database_url is None
    assert config.observability.mlflow.enabled is False


def test_process_config_carries_recursion_settings() -> None:
    from fleet_rlm.integrations.config.process import ProcessConfig

    config = ProcessConfig.model_validate(
        {
            "rlm": {
                "recursion": {
                    "max_depth": 3,
                    "delegate_max_calls_per_turn": 4,
                    "child_fork_fallback": "fail",
                },
            }
        }
    )

    assert config.rlm.recursion.max_depth == 3
    assert config.rlm.recursion.delegate_max_calls_per_turn == 4
    assert config.rlm.recursion.child_fork_fallback == "fail"
