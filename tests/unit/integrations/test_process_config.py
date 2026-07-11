from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def test_missing_yaml_uses_typed_defaults(tmp_path: Path) -> None:
    from fleet_rlm.integrations.config.process import load_process_config

    resolved = load_process_config(tmp_path / "missing.yaml", environ={})

    assert resolved.config.rlm.max_iters == 20
    assert resolved.config.daytona.pool.max_concurrent_sandboxes == 5
    assert resolved.config.observability.mlflow.enabled is False


def test_default_loader_uses_packaged_config() -> None:
    from fleet_rlm.integrations.config.process import load_process_config, packaged_config_path

    resolved = load_process_config(environ={})

    assert resolved.config_path == packaged_config_path()
    assert resolved.sources["rlm.max_iters"] == "yaml"


def test_yaml_loads_and_environment_aliases_win(tmp_path: Path) -> None:
    from fleet_rlm.integrations.config.process import load_process_config

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
llm:
  roles:
    planner:
      model: yaml/model
rlm:
  max_iters: 9
daytona:
  pool:
    max_concurrent_sandboxes: 3
observability:
  mlflow:
    enabled: false
""".strip(),
        encoding="utf-8",
    )

    resolved = load_process_config(
        config_path,
        environ={
            "DSPY_LM_MODEL": "env/model",
            "FLEET_MAX_CONCURRENT_SANDBOXES": "7",
            "MLFLOW_ENABLED": "true",
            "MLFLOW_EXPERIMENT_NAME": "environment-experiment",
        },
    )

    assert resolved.config.llm.roles.planner.model == "env/model"
    assert resolved.config.rlm.max_iters == 9
    assert resolved.config.daytona.pool.max_concurrent_sandboxes == 7
    assert resolved.config.observability.mlflow.enabled is True
    assert resolved.config.observability.mlflow.experiment_name == "environment-experiment"
    assert resolved.sources["llm.roles.planner.model"] == "environment"
    assert resolved.sources["rlm.max_iters"] == "yaml"


def test_unknown_yaml_key_fails_closed(tmp_path: Path) -> None:
    from fleet_rlm.integrations.config.process import load_process_config

    config_path = tmp_path / "config.yaml"
    config_path.write_text("rlm:\n  max_iterz: 4\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"rlm\.max_iterz"):
        load_process_config(config_path, environ={})


def test_unknown_yaml_error_does_not_echo_value(tmp_path: Path) -> None:
    from fleet_rlm.integrations.config.process import load_process_config

    config_path = tmp_path / "config.yaml"
    config_path.write_text("unexpected: TOPSECRET123\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_process_config(config_path, environ={})

    assert "unexpected" in str(exc_info.value)
    assert "TOPSECRET123" not in str(exc_info.value)


def test_canonical_and_legacy_overrides_are_typed() -> None:
    from fleet_rlm.integrations.config.process import load_process_config

    resolved = load_process_config(
        environ={},
        overrides=(
            "llm.roles.planner.model=openai/canonical",
            "sandbox.timeout=321",
            "rlm_settings.max_llm_calls=17",
        ),
    )

    assert resolved.config.llm.roles.planner.model == "openai/canonical"
    assert resolved.config.daytona.execution_timeout_s == 321
    assert resolved.config.rlm.max_llm_calls == 17
    assert resolved.sources["daytona.execution_timeout_s"] == "override"


def test_unknown_override_fails_closed() -> None:
    from fleet_rlm.integrations.config.process import load_process_config

    with pytest.raises(ValueError, match="Unknown configuration override path"):
        load_process_config(environ={}, overrides=("rlm.max_callz=3",))


def test_yaml_rejects_secret_bearing_database_url(tmp_path: Path) -> None:
    from fleet_rlm.integrations.config.process import load_process_config

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "persistence:\n  database_url: postgresql://user:secret@example.test/db\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Secret configuration field is not allowed"):
        load_process_config(config_path, environ={})

    resolved = load_process_config(
        environ={"DATABASE_URL": "postgresql://user:secret@example.test/db"},
    )
    assert resolved.config.persistence.database_url.endswith("/db")


def test_yaml_rejects_credential_bearing_mlflow_uri(tmp_path: Path) -> None:
    from fleet_rlm.integrations.config.process import load_process_config

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "observability:\n  mlflow:\n    tracking_uri: https://user:secret@example.test/api\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="contains credentials"):
        load_process_config(config_path, environ={})


@pytest.mark.parametrize(
    "yaml_text,field_name",
    [
        ("llm:\n  openai_api_key: TOPSECRET123\n", "llm.openai_api_key"),
        ("daytona:\n  api_key: TOPSECRET123\n", "daytona.api_key"),
        ("api:\n  dev_jwt_secret: TOPSECRET123\n", "api.dev_jwt_secret"),
        ("observability:\n  posthog_api_key: TOPSECRET123\n", "observability.posthog_api_key"),
        ("observability:\n  mlflow_tracking_token: TOPSECRET123\n", "observability.mlflow_tracking_token"),
    ],
)
def test_yaml_secret_field_errors_never_echo_values(tmp_path: Path, yaml_text: str, field_name: str) -> None:
    from fleet_rlm.integrations.config.process import load_process_config

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml_text, encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_process_config(config_path, environ={})

    assert field_name in str(exc_info.value)
    assert "TOPSECRET123" not in str(exc_info.value)


def test_diagnostics_report_sources_without_secrets(tmp_path: Path) -> None:
    from fleet_rlm.integrations.config.process import load_process_config

    resolved = load_process_config(
        environ={
            "DATABASE_URL": "postgresql://user:secret@example.test/db",
            "DSPY_LM_MODEL": "openai/gpt-test",
            "MLFLOW_TRACKING_URI": "https://mlflow-user:mlflow-secret@example.test/api?token=secret#fragment",
        }
    )

    diagnostics = resolved.diagnostics()
    assert diagnostics["llm.roles.planner.model"] == {
        "value": "openai/gpt-test",
        "source": "environment",
    }
    assert "secret" not in repr(diagnostics)
    assert "persistence.database_url" not in diagnostics
    assert diagnostics["observability.mlflow.tracking_uri"]["value"] == "https://example.test/api"


def test_import_has_no_runtime_client_side_effects() -> None:
    blocked = {"dspy", "daytona", "mlflow", "fastapi", "sqlalchemy"}
    before = set(sys.modules)

    sys.modules.pop("fleet_rlm.integrations.config.process", None)
    importlib.import_module("fleet_rlm.integrations.config.process")

    imported = set(sys.modules) - before
    assert not {name for name in imported if name.split(".", 1)[0] in blocked}
