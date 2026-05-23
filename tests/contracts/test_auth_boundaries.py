from __future__ import annotations

import pytest


@pytest.mark.parametrize("path", ["/api/v1/auth/me", "/api/v1/sessions/state"])
def test_auth_boundaries_reject_missing_credentials(no_db_client, path: str) -> None:
    no_db_client.app.state.config_deps.config.auth_required = True

    response = no_db_client.get(path)

    assert response.status_code in {401, 403}


@pytest.mark.parametrize("path", ["/api/v1/auth/me", "/api/v1/sessions/state"])
def test_auth_boundaries_accept_debug_credentials_in_local_mode(
    no_db_client,
    auth_headers: dict[str, str],
    path: str,
) -> None:
    no_db_client.app.state.config_deps.config.auth_required = True

    response = no_db_client.get(path, headers=auth_headers)

    assert response.status_code == 200
