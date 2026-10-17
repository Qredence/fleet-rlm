from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from fleet_rlm.session import SessionRuntime


def test_lost_session_lease_cancels_remote_work_and_fences_runtime() -> None:
    probed = threading.Event()
    cancelled = threading.Event()

    class LostLease:
        def execute(self, _statement: str) -> None:
            probed.set()
            raise ConnectionError("lease connection dropped")

        def close(self) -> None:
            return None

    runtime = SessionRuntime(
        sandbox_mgr=SimpleNamespace(cancel_turn=cancelled.set),
        harness=SimpleNamespace(),
        skill_mgr=SimpleNamespace(),
        engine=SimpleNamespace(),
        lease=LostLease(),  # type: ignore[arg-type]
    )
    runtime.start_lease_monitor(interval_seconds=0.001)

    assert probed.wait(timeout=1)
    assert cancelled.wait(timeout=1)
    deadline = time.monotonic() + 1
    while not runtime.lease_lost.is_set() and time.monotonic() < deadline:
        time.sleep(0.001)

    with pytest.raises(RuntimeError, match="ownership lease was lost"):
        runtime.assert_lease_held()

    runtime.close_lease()
