"""Unit contracts for the policy-controlled PostHog analytics client."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from fleet_rlm.config.settings import Settings
from fleet_rlm.observability.posthog import (
    _load_or_create_instance_id,
    get_client,
    get_distinct_id,
    init_posthog,
    shutdown_posthog,
)


def _fake_posthog(created: list[tuple[str, str | None, bool]], shutdowns: list[str] | None = None) -> object:
    """
    Create a mock PostHog constructor that records client creation and shutdown events.

    Parameters:
        created (list[tuple[str, str | None, bool]]): Collection receiving client initialization arguments.
        shutdowns (list[str] | None): Collection receiving tokens when mock clients shut down.

    Returns:
        object: Callable mock constructor for creating clients with a shutdown method.
    """

    def build(token: str, host: str | None = None, enable_exception_autocapture: bool = True) -> object:
        created.append((token, host, enable_exception_autocapture))
        if shutdowns is None:
            return SimpleNamespace(shutdown=lambda: None)
        return SimpleNamespace(shutdown=lambda: shutdowns.append(token))

    return build


def test_init_disabled_policy_leaves_client_disabled(monkeypatch) -> None:
    shutdown_posthog()
    created: list[tuple[str, str | None, bool]] = []
    monkeypatch.setattr("fleet_rlm.observability.posthog.Posthog", _fake_posthog(created))

    init_posthog(Settings(posthog_enabled=False))

    assert get_client() is None
    assert created == []


def test_init_enabled_without_token_stays_disabled(monkeypatch) -> None:
    shutdown_posthog()
    created: list[tuple[str, str | None, bool]] = []
    monkeypatch.setattr("fleet_rlm.observability.posthog.Posthog", _fake_posthog(created))

    init_posthog(Settings(posthog_enabled=True))

    assert get_client() is None
    assert created == []


def test_reinit_with_disabled_policy_shuts_down_previous_client(monkeypatch, tmp_path: Path) -> None:
    shutdown_posthog()
    created: list[tuple[str, str | None, bool]] = []
    shutdowns: list[str] = []
    monkeypatch.setattr("fleet_rlm.observability.posthog.Posthog", _fake_posthog(created, shutdowns))
    settings = Settings(
        posthog_enabled=True,
        posthog_project_token="phc-test-token",
        data_root=str(tmp_path),
    )

    init_posthog(settings)
    assert get_client() is not None

    init_posthog(Settings(posthog_enabled=False))

    assert get_client() is None
    assert shutdowns == ["phc-test-token"]
    shutdown_posthog()


def test_posthog_host_must_be_absolute_http_url() -> None:
    with pytest.raises(ValidationError):
        Settings(posthog_host="eu.i.posthog.com")


def test_posthog_host_normalizes_trailing_slash() -> None:
    settings = Settings(posthog_host="https://eu.i.posthog.com/")

    assert settings.posthog_host == "https://eu.i.posthog.com"


def test_init_enabled_with_token_creates_client_and_disables_exception_autocapture(monkeypatch, tmp_path: Path) -> None:
    shutdown_posthog()
    created: list[tuple[str, str | None, bool]] = []
    monkeypatch.setattr("fleet_rlm.observability.posthog.Posthog", _fake_posthog(created))

    init_posthog(
        Settings(
            posthog_enabled=True,
            posthog_project_token="phc-test-token",
            posthog_host="https://eu.i.posthog.com",
            data_root=str(tmp_path),
        )
    )

    assert get_client() is not None
    assert created == [("phc-test-token", "https://eu.i.posthog.com", False)]
    shutdown_posthog()


def test_distinct_id_is_stable_and_persisted_across_restarts(monkeypatch, tmp_path: Path) -> None:
    shutdown_posthog()
    created: list[tuple[str, str | None, bool]] = []
    monkeypatch.setattr("fleet_rlm.observability.posthog.Posthog", _fake_posthog(created))
    settings = Settings(
        posthog_enabled=True,
        posthog_project_token="phc-test-token",
        data_root=str(tmp_path),
    )

    init_posthog(settings)
    first = get_distinct_id()
    assert first
    assert first != "local_operator"
    shutdown_posthog()

    init_posthog(settings)
    assert get_distinct_id() == first
    shutdown_posthog()


def test_distinct_id_is_persisted_instance_id_not_deterministic_local_user_id(monkeypatch, tmp_path: Path) -> None:
    shutdown_posthog()
    created: list[tuple[str, str | None, bool]] = []
    monkeypatch.setattr("fleet_rlm.observability.posthog.Posthog", _fake_posthog(created))
    settings = Settings(
        posthog_enabled=True,
        posthog_project_token="phc-test-token",
        data_root=str(tmp_path),
    )

    init_posthog(settings)

    stored = (tmp_path / "analytics-instance-id").read_text(encoding="utf-8").strip()
    assert stored == get_distinct_id()
    assert stored != "fleet-rlm/local-user"
    shutdown_posthog()


def test_load_or_create_instance_id_reads_existing_file(tmp_path: Path) -> None:
    instance_id = "persisted-instance-id"
    (tmp_path / "analytics-instance-id").write_text(f"{instance_id}\n", encoding="utf-8")

    assert _load_or_create_instance_id(str(tmp_path)) == instance_id


def test_load_or_create_instance_id_writes_fresh_id(tmp_path: Path) -> None:
    instance_id = _load_or_create_instance_id(str(tmp_path))

    assert instance_id
    assert (tmp_path / "analytics-instance-id").read_text(encoding="utf-8").strip() == instance_id


def test_default_profile_enables_posthog_and_resolves_token(monkeypatch) -> None:
    import fleet_rlm.config.loader as config

    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "test-daytona-key")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-databricks-token")
    monkeypatch.setenv("FLEET_DATABRICKS_AI_GATEWAY_BASE_URL", "https://gateway.example.test/v1")
    monkeypatch.setenv("FLEET_MODAL_API_KEY", "test-modal-key")
    monkeypatch.setenv("FLEET_MODAL_BASE_URL", "https://modal.example.test/v1")
    monkeypatch.setenv("POSTHOG_PROJECT_TOKEN", "phc-policy-token")

    settings = config.load_runtime_settings()

    assert settings.posthog_enabled is True
    assert settings.posthog_host == "https://eu.i.posthog.com"
    assert settings.posthog_project_token is not None
    assert settings.posthog_project_token.get_secret_value() == "phc-policy-token"


def test_benchmark_profiles_disable_posthog() -> None:
    from fleet_rlm.config.loader import _deep_merge, _flatten_policy, _read_policy_document

    document = _read_policy_document(Path("config/fleet.toml"))

    for profile in ("daytona-bench", "daytona-bench-40"):
        flattened = _flatten_policy(_deep_merge(document.defaults, document.profiles[profile]))
        assert flattened.settings["posthog_enabled"] is False
