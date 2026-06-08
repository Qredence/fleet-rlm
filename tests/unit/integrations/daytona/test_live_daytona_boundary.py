from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live_daytona


@pytest.mark.asyncio
async def test_daytona_code_run_smoke() -> None:
    if not os.environ.get("DAYTONA_API_KEY"):
        pytest.skip("DAYTONA_API_KEY not configured")

    from daytona import Daytona

    daytona = Daytona()
    sandbox = daytona.create()
    try:
        response = sandbox.process.code_run('print("fleet-rlm-boundary-ok")')
        assert response.exit_code == 0
        assert "fleet-rlm-boundary-ok" in (response.result or "")
    finally:
        sandbox.delete()
