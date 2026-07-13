"""Opt-in live proof that REPL state persists across interpreter executes.

Excluded from default ``make test`` via the ``live_daytona`` marker.
Requires ``FLEET_DAYTONA_API_KEY``.
"""

from __future__ import annotations

import os

import pytest

from fleet_rlm.config import Settings
from fleet_rlm.daytona.client import build_daytona_client
from fleet_rlm.daytona.interpreter import DaytonaCodeInterpreter, sandbox_backend
from fleet_rlm.daytona.leases import InterpreterLease

pytestmark = pytest.mark.live_daytona


def _have_daytona_creds() -> bool:
    return bool(os.environ.get("FLEET_DAYTONA_API_KEY"))


def test_python_state_persists_across_active_interpreter_calls() -> None:
    if not _have_daytona_creds():
        pytest.skip("FLEET_DAYTONA_API_KEY not configured")

    settings = Settings()

    client = build_daytona_client(settings)
    sandbox = client.create()
    lease = InterpreterLease(
        sandbox_id=getattr(sandbox, "id", "unknown"),
        interpreter_id="live-1",
        volume_id="none",
        mount_path="/home/daytona/memory",
        interpreter=DaytonaCodeInterpreter(backend=sandbox_backend(sandbox)),
    )
    try:
        lease.interpreter.start()
        lease.interpreter.execute("fleet_fleet_marker = 'persist-ok'")
        result = lease.interpreter.execute("print(fleet_fleet_marker)")
        assert "persist-ok" in result
    finally:
        lease.release()
        lease.release()
        sandbox.delete()
