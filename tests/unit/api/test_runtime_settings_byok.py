"""BYOK Daytona settings PATCH masking-roundtrip tests.

Verifies that resubmitting the masked display value for ``DAYTONA_API_KEY`` (the
form returned by ``GET /runtime/settings``) does not overwrite the stored
encrypted credential, while genuine new values and explicit clears behave
correctly.
"""

from __future__ import annotations

import warnings
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from fleet_rlm.api.routers import runtime as runtime_router
from fleet_rlm.api.schemas.runtime import RuntimeSettingsUpdateRequest
from fleet_rlm.integrations.config.env_file import mask_secret
from fleet_rlm.integrations.database.fleet_repository import FleetRepository
from fleet_rlm.integrations.llm_profiles.crypto import decrypt_api_key, encrypt_api_key


class _FakeFleetRepository(FleetRepository):
    """In-memory FleetRepository stand-in for the BYOK Daytona path."""

    def __init__(self) -> None:  # noqa: D401 - skip db_manager wiring
        # Satisfy RepositoryContextMixin.__init__ with a stub; the fake
        # overrides every method that would touch a real DatabaseManager.
        super().__init__(database=SimpleNamespace())
        self._settings: dict[str, str] = {}

    async def get_workspace_runtime_setting(self, *, tenant_id, workspace_id) -> dict:  # type: ignore[override]
        return dict(self._settings)

    async def upsert_workspace_runtime_setting(  # type: ignore[override]
        self, *, tenant_id, workspace_id, user_id, settings_json
    ) -> None:
        self._settings = dict(settings_json)


def _make_deps(secret_key: str, repo: _FakeFleetRepository, identity):
    config = SimpleNamespace(
        app_env="production",
        secret_encryption_key=secret_key,
        env_path=None,
    )
    config_deps = SimpleNamespace(config=config)
    persistence_deps = SimpleNamespace(repository=repo)
    return config_deps, persistence_deps


@pytest.mark.asyncio
async def test_masked_daytona_key_resubmit_does_not_corrupt_stored_key() -> None:
    from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult

    secret_key = Fernet.generate_key().decode("ascii")
    real_key = "sk-real-production-key-1234"

    repo = _FakeFleetRepository()
    repo._settings["DAYTONA_API_KEY"] = encrypt_api_key(real_key, secret=secret_key)
    repo._settings["DAYTONA_API_URL"] = "https://daytona.example.com"

    identity = IdentityUpsertResult(tenant_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())
    config_deps, persistence_deps = _make_deps(secret_key, repo, identity)

    # Client resubmits the masked display value the snapshot returned.
    masked = mask_secret(real_key)
    request = RuntimeSettingsUpdateRequest(updates={"DAYTONA_API_KEY": masked})

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning is a failure
        response = await runtime_router.patch_runtime_settings(
            config_deps=config_deps,
            lm_deps=None,
            persistence_deps=persistence_deps,
            persisted_identity=identity,
            diagnostics_deps=None,
            request=request,
        )

    assert response.skipped == ["DAYTONA_API_KEY"]
    # Stored encrypted value is unchanged -> still decrypts to the real key.
    assert decrypt_api_key(repo._settings["DAYTONA_API_KEY"], secret=secret_key) == real_key
    # Non-secret sibling value is also untouched.
    assert repo._settings["DAYTONA_API_URL"] == "https://daytona.example.com"


@pytest.mark.asyncio
async def test_new_daytona_key_is_encrypted_and_persisted() -> None:
    from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult

    secret_key = Fernet.generate_key().decode("ascii")
    repo = _FakeFleetRepository()
    repo._settings["DAYTONA_API_KEY"] = encrypt_api_key("sk-old-key-999", secret=secret_key)

    identity = IdentityUpsertResult(tenant_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())
    config_deps, persistence_deps = _make_deps(secret_key, repo, identity)

    request = RuntimeSettingsUpdateRequest(updates={"DAYTONA_API_KEY": "sk-rotated-key-42"})

    response = await runtime_router.patch_runtime_settings(
        config_deps=config_deps,
        lm_deps=None,
        persistence_deps=persistence_deps,
        persisted_identity=identity,
        diagnostics_deps=None,
        request=request,
    )

    assert response.updated == ["DAYTONA_API_KEY"]
    assert decrypt_api_key(repo._settings["DAYTONA_API_KEY"], secret=secret_key) == "sk-rotated-key-42"


@pytest.mark.asyncio
async def test_empty_daytona_key_when_existing_is_skipped_not_cleared() -> None:
    """An empty PATCH value must NOT wipe a stored non-empty key.

    This guards against a GET decrypt failure surfacing as "" in the
    snapshot, which would otherwise be round-tripped back and erase the
    real credential on the next save.
    """
    from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult

    secret_key = Fernet.generate_key().decode("ascii")
    real_key = "sk-keep-me-alive"
    repo = _FakeFleetRepository()
    repo._settings["DAYTONA_API_KEY"] = encrypt_api_key(real_key, secret=secret_key)

    identity = IdentityUpsertResult(tenant_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())
    config_deps, persistence_deps = _make_deps(secret_key, repo, identity)

    request = RuntimeSettingsUpdateRequest(updates={"DAYTONA_API_KEY": ""})

    response = await runtime_router.patch_runtime_settings(
        config_deps=config_deps,
        lm_deps=None,
        persistence_deps=persistence_deps,
        persisted_identity=identity,
        diagnostics_deps=None,
        request=request,
    )

    assert response.skipped == ["DAYTONA_API_KEY"]
    assert response.updated == []
    # Stored encrypted value is preserved.
    assert decrypt_api_key(repo._settings["DAYTONA_API_KEY"], secret=secret_key) == real_key


@pytest.mark.asyncio
async def test_empty_daytona_key_clears_when_no_existing_value() -> None:
    """An empty PATCH value is still treated as an explicit clear when there
    is no existing stored value (idempotent clear on a fresh record)."""
    from fleet_rlm.integrations.database.repository_identity import IdentityUpsertResult

    secret_key = Fernet.generate_key().decode("ascii")
    repo = _FakeFleetRepository()
    # No pre-existing DAYTONA_API_KEY in settings.

    identity = IdentityUpsertResult(tenant_id=uuid4(), user_id=uuid4(), workspace_id=uuid4())
    config_deps, persistence_deps = _make_deps(secret_key, repo, identity)

    request = RuntimeSettingsUpdateRequest(updates={"DAYTONA_API_KEY": ""})

    response = await runtime_router.patch_runtime_settings(
        config_deps=config_deps,
        lm_deps=None,
        persistence_deps=persistence_deps,
        persisted_identity=identity,
        diagnostics_deps=None,
        request=request,
    )

    assert response.updated == ["DAYTONA_API_KEY"]
    assert repo._settings.get("DAYTONA_API_KEY", "") == ""
