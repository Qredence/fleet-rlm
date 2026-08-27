"""Late recursive-child acquisition ownership tests."""

from __future__ import annotations

from concurrent.futures import Future
from threading import Event

import pytest

from fleet_rlm.daytona import recursive_child_runtime
from fleet_rlm.rlm.recursion import ChildRuntimeCleanupError


def test_thread_start_failure_dispatches_cleanup_without_loop_thread_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = Event()
    release = Event()

    def close(_lease: object) -> None:
        """
        Signal that cleanup was invoked and wait for release.
        """
        started.set()
        assert release.wait(2)

    class FailingThread:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            raise RuntimeError("thread start failed")

    monkeypatch.setattr(recursive_child_runtime, "Thread", FailingThread)
    owner = recursive_child_runtime.LateCleanupOwner(wait_timeout_s=1.0)
    acquisition: Future[object] = Future()
    acquisition.set_result(object())

    owner.adopt_late_acquisition(acquisition, close)
    assert started.wait(2)

    release.set()
    with pytest.raises(ChildRuntimeCleanupError, match="recursive child cleanup failed"):
        owner.wait_owned()
