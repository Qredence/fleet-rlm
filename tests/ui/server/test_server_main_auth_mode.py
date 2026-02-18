from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fleet_rlm.server.config import ServerRuntimeConfig
from fleet_rlm.server.main import create_app


def test_auth_mode_entra_fails_fast_on_startup():
    app = create_app(config=ServerRuntimeConfig(auth_mode="entra"))
    with pytest.raises(RuntimeError, match="AUTH_MODE=entra is configured"):
        with TestClient(app):
            pass
