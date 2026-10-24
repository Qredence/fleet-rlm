"""A controlled restart fences both Turns and synchronous workspace operations."""

import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from fleet_rlm.api.deps import session_lock
from fleet_rlm.config import FleetSettings
from fleet_rlm.session import SessionRuntimeRegistry


def test_registry_fences_admission_and_preserves_active_work():
    registry = SessionRuntimeRegistry(FleetSettings(_env_file=None), None)
    lock = threading.Lock()
    registry._sessions["one"] = SimpleNamespace(turn_lock=lock)
    lock.acquire()
    assert not registry.request_restart()
    assert not registry._restart_requested.is_set()
    lock.release()
    assert registry.request_restart()
    with pytest.raises(RuntimeError, match="restarting"):
        registry.get("two")


@pytest.mark.parametrize("race", [False, True])
def test_workspace_lock_rejects_work_before_and_after_restart_fence(race):
    restarting = threading.Event()
    lock = threading.Lock()

    class Lock:
        def acquire(self, **_kwargs):
            lock.acquire()
            if race:
                restarting.set()
            return True

        def release(self):
            lock.release()

    if not race:
        restarting.set()
    runtime = SimpleNamespace(turn_lock=Lock(), restart_requested=restarting, assert_lease_held=lambda: None)
    with pytest.raises(HTTPException) as error, session_lock(runtime):
        pytest.fail("workspace work must not start")
    assert error.value.status_code == 503
    assert not lock.locked()
