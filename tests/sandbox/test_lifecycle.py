"""Sandbox lifecycle: create parameters against the pinned SDK, root rebuild, stuck creates, lost children, cancel."""

from __future__ import annotations

import importlib.metadata
import threading
import time
import tomllib
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from daytona import CreateSandboxFromSnapshotParams, Daytona
from daytona.common.errors import (
    DaytonaBadRequestError,
    DaytonaConnectionError,
    DaytonaError,
    DaytonaNotFoundError,
    DaytonaTimeoutError,
)

from fleet_rlm.config import FleetSettings
from fleet_rlm.sandbox import repl
from fleet_rlm.sandbox.manager import DaytonaSandboxManager, SandboxUnavailableError
from fleet_rlm.sandbox.repl import SandboxLostError

PINNED_DAYTONA = "0.224.0"


def _root(**kwargs: Any) -> DaytonaSandboxManager:
    return DaytonaSandboxManager("key", "org", "volume", "life", **kwargs)


def _child(root: DaytonaSandboxManager) -> DaytonaSandboxManager:
    return DaytonaSandboxManager("key", "org", "volume", "life", parent=root, auto_stop_minutes=root.auto_stop_minutes)


class _CapturedError(Exception):
    """Raised by the fake API once the SDK has built its request body."""


def _sdk_request_body(params: CreateSandboxFromSnapshotParams) -> Any:
    """Run the pinned SDK's own client-side validation and request mapping, stopping before the network."""
    captured: list[Any] = []

    def create_sandbox(body: Any, **_kwargs: Any) -> Any:
        captured.append(body)
        raise _CapturedError

    fake_client = SimpleNamespace(_target=None, _sandbox_api=SimpleNamespace(create_sandbox=create_sandbox))
    fake_client._validate_language_label = lambda label: Daytona._validate_language_label(fake_client, label)  # type: ignore[attr-defined]
    with pytest.raises(_CapturedError):
        Daytona._create(fake_client, params, timeout=60)  # type: ignore[arg-type]
    return captured[0]


def test_the_pinned_daytona_sdk_is_installed() -> None:
    project = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    assert f"daytona=={PINNED_DAYTONA}" in project["project"]["dependencies"]
    assert importlib.metadata.version("daytona") == PINNED_DAYTONA


@pytest.mark.parametrize("kind", ["root", "disposable_root", "child"])
@pytest.mark.parametrize("turn_timeout", [900, 3600])
def test_create_params_pass_the_pinned_sdk_validation(kind: str, turn_timeout: int) -> None:
    root = _root(turn_timeout_seconds=turn_timeout, disposable=kind == "disposable_root")
    manager = _child(root) if kind == "child" else root
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # e.g. the SDK's ephemeral/auto_delete_interval conflict warning
        params = manager.create_params(linked_sandbox="root-sandbox" if kind == "child" else None)
        assert isinstance(params, CreateSandboxFromSnapshotParams)
        assert set(params.model_fields_set) <= set(CreateSandboxFromSnapshotParams.model_fields)
        body = _sdk_request_body(params)
    assert body.snapshot == "fleet-rlm-base"
    assert body.auto_stop_interval == params.auto_stop_interval
    assert body.auto_delete_interval == params.auto_delete_interval
    assert body.ttl_minutes == params.ttl_minutes
    assert [volume.mount_path for volume in body.volumes] == ["/workspace"]
    assert body.labels["fleet_agent"] == manager.agent_id


@pytest.mark.parametrize(
    ("turn_timeout", "auto_stop", "ttl", "stop"),
    [(900, 30, 30, 30), (3600, 30, 70, 65), (900, 90, 30, 90), (61, 1, 30, 7)],
)
def test_child_ttl_and_auto_stop_outlive_the_turn_deadline(turn_timeout, auto_stop, ttl, stop) -> None:
    root = _root(turn_timeout_seconds=turn_timeout, auto_stop_minutes=auto_stop)
    params = _child(root).create_params(linked_sandbox="root-sandbox")
    # ttl = max(30, ceil(turn/60) + 10); auto-stop = max(auto_stop_minutes, ceil(turn/60) + 5)
    assert (params.ttl_minutes, params.auto_stop_interval) == (ttl, stop)
    assert params.ephemeral is True and params.auto_delete_interval == 0
    assert params.linked_sandbox == "root-sandbox"


@pytest.mark.parametrize("turn_timeout", [900, 3600])
def test_root_has_no_ttl_and_a_configurable_auto_delete(turn_timeout: int) -> None:
    params = _root(turn_timeout_seconds=turn_timeout).create_params()
    assert params.ttl_minutes is None and "ttl_minutes" not in params.model_dump(exclude_none=True)
    assert params.ephemeral is False and params.auto_delete_interval == 7 * 24 * 60
    assert params.auto_stop_interval == 30
    assert _root(root_auto_delete_minutes=60).create_params().auto_delete_interval == 60
    assert _root(root_auto_delete_minutes=-1).create_params().auto_delete_interval == -1
    disposable = _root(turn_timeout_seconds=turn_timeout, disposable=True).create_params()
    assert disposable.ephemeral is True and disposable.auto_delete_interval == 0 and disposable.ttl_minutes is None


def test_root_auto_delete_setting_defaults_to_seven_days_and_refuses_immediate_deletion(monkeypatch) -> None:
    monkeypatch.delenv("FLEET_DAYTONA_ROOT_AUTO_DELETE_MINUTES", raising=False)
    assert FleetSettings().daytona_root_auto_delete_minutes == 10_080
    monkeypatch.setenv("FLEET_DAYTONA_ROOT_AUTO_DELETE_MINUTES", "-1")
    assert FleetSettings().daytona_root_auto_delete_minutes == -1
    for invalid in (0, -2):
        with pytest.raises(ValueError):
            FleetSettings(daytona_root_auto_delete_minutes=invalid)
        with pytest.raises(ValueError):
            _root(root_auto_delete_minutes=invalid)


def test_a_missing_root_sandbox_is_rebuilt_from_the_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    def gone() -> None:
        raise DaytonaNotFoundError("Sandbox not found", status_code=404)

    missing = SimpleNamespace(id="sandbox-missing", state="started", refresh_data=gone)
    rebuilt = SimpleNamespace(id="sandbox-new", state="started")
    created: list[CreateSandboxFromSnapshotParams] = []
    root = _root(shared_notes_subpath="shared/learned-notes")
    root._sandbox = missing
    root._contexts["main"] = object()
    root._client = SimpleNamespace(
        create=lambda params: created.append(params) or rebuilt,
        delete=lambda *_a, **_k: pytest.fail("a missing sandbox cannot be deleted"),
        list=lambda _query: [],
    )
    assert root.ensure_sandbox() is rebuilt
    assert root._contexts == {}
    assert [(mount.mount_path, mount.subpath) for mount in created[0].volumes] == [
        ("/workspace", "sessions/life"),
        ("/learned", "shared/learned-notes"),
    ]


def test_a_missing_child_sandbox_raises_instead_of_rebuilding() -> None:
    def gone() -> None:
        raise DaytonaNotFoundError("Sandbox not found", status_code=404)

    child = _child(_root())
    child._sandbox = SimpleNamespace(id="child-missing", state="started", refresh_data=gone)
    child._client = SimpleNamespace(create=lambda _params: pytest.fail("no rebuild"))
    with pytest.raises(SandboxUnavailableError, match="'missing'"):
        child.ensure_sandbox()


def _creating_root(failure: BaseException, adopted: Any | None = None) -> tuple[DaytonaSandboxManager, list[str]]:
    calls: list[str] = []
    fresh = SimpleNamespace(id="sandbox-fresh", state="started")

    def create(_params: Any) -> Any:
        calls.append("create")
        if calls.count("create") == 1:
            raise failure
        return fresh

    def list_sandboxes(_query: Any) -> list[Any]:
        calls.append("list")
        return [adopted] if adopted is not None else []

    root = _root()
    root._client = SimpleNamespace(create=create, list=list_sandboxes)
    return root, calls


@pytest.mark.parametrize(
    "failure",
    [
        DaytonaBadRequestError("snapshot not found", status_code=400),
        DaytonaBadRequestError("auto_stop_interval must be a non-negative integer"),  # client-side, no status
        DaytonaError("forbidden", status_code=403),
        DaytonaError("quota exceeded", status_code=422),
    ],
    ids=["400", "client_validation", "403", "422"],
)
def test_a_definite_create_failure_clears_the_pending_flag(monkeypatch, tmp_path, failure) -> None:
    monkeypatch.chdir(tmp_path)
    root, calls = _creating_root(failure)
    with pytest.raises(DaytonaError):
        root.ensure_sandbox()
    assert root._creation_pending is False
    assert not (tmp_path / ".fleet/allocations" / f"{root.agent_id}.json").exists()
    assert root.ensure_sandbox().id == "sandbox-fresh"
    assert calls == ["create", "create"]


@pytest.mark.parametrize(
    "failure",
    [
        DaytonaTimeoutError("create timed out"),
        DaytonaConnectionError("connection reset"),
        DaytonaError("bad gateway", status_code=502),
        DaytonaError("request timeout", status_code=408),
        OSError("socket closed"),
    ],
    ids=["timeout", "connection", "502", "408", "unknown"],
)
def test_an_uncertain_create_failure_keeps_the_pending_flag_and_never_duplicates(monkeypatch, tmp_path, failure):
    monkeypatch.chdir(tmp_path)
    adopted = SimpleNamespace(id="sandbox-adopted", state="started")
    root, calls = _creating_root(failure, adopted)
    with pytest.raises(type(failure)):
        root.ensure_sandbox()
    assert root._creation_pending is True
    assert root.ensure_sandbox() is adopted
    assert root._creation_pending is False and calls == ["create", "list"]


def test_an_uncertain_create_with_no_single_match_still_refuses_a_duplicate(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    root, calls = _creating_root(DaytonaTimeoutError("create timed out"))
    with pytest.raises(DaytonaTimeoutError):
        root.ensure_sandbox()
    with pytest.raises(RuntimeError, match="refusing a duplicate create"):
        root.ensure_sandbox()
    assert calls == ["create", "list"] and root._creation_pending is True


class _DyingSandbox(SimpleNamespace):
    """A started child sandbox whose provider state turns 'destroyed' while run_code blocks forever."""

    def __init__(self, release: threading.Event) -> None:
        super().__init__(id="child-dying", state="started", refreshes=0)
        self.release = release
        self.code_interpreter = SimpleNamespace(run_code=self._run_code)

    def refresh_data(self) -> None:
        self.refreshes += 1
        # The first refresh is ensure_sandbox's check; the sandbox dies once the execution is running.
        self.state = "started" if self.refreshes == 1 else "destroyed"

    def _run_code(self, *_args: Any, **_kwargs: Any) -> Any:
        self.release.wait(30)  # the websocket never notices: only the execution timeout would end it
        raise DaytonaConnectionError("websocket closed")


def test_a_child_sandbox_destroyed_mid_run_code_raises_promptly(monkeypatch) -> None:
    monkeypatch.setattr(repl, "LIVENESS_POLL_SECONDS", 0.01)
    release = threading.Event()
    stopped: list[str] = []
    root = _root(turn_timeout_seconds=900)
    root._client = SimpleNamespace(stop=lambda sandbox, **_k: stopped.append(sandbox.id))
    root._sandbox = SimpleNamespace(id="root-sandbox", state="started")
    child = _child(root)
    child._client = SimpleNamespace()
    child._sandbox = _DyingSandbox(release)
    interpreter = SimpleNamespace(ns_id="ns_child", answer_host_call=lambda _call: {})
    child._contexts[interpreter.ns_id] = object()
    root.reset_turn_budget()
    try:
        started = time.monotonic()
        with pytest.raises(SandboxLostError):
            child.run_namespace(interpreter, {"code": "while True: pass"})  # type: ignore[arg-type]
        assert time.monotonic() - started < 5, "a lost child must not wait for the 900 s execution timeout"
        assert stopped == ["root-sandbox"], "an unknown outcome still stops remote work"
    finally:
        release.set()
        root.end_turn()


def test_a_watched_execution_returns_its_result_and_keeps_the_caller_context(monkeypatch) -> None:
    import contextvars

    monkeypatch.setattr(repl, "LIVENESS_POLL_SECONDS", 0.01)
    marker: contextvars.ContextVar[str] = contextvars.ContextVar("marker", default="unset")
    marker.set("caller")
    seen: list[str] = []

    def run_code(_code: str, *, context: Any, on_stdout: Any, timeout: int) -> Any:  # noqa: ARG001
        time.sleep(0.05)
        seen.append(marker.get())
        on_stdout(SimpleNamespace(output=repl.RESULT_MARK + '{"stdout": "ok"}\n'))
        return SimpleNamespace(error=None)

    sandbox = SimpleNamespace(code_interpreter=SimpleNamespace(run_code=run_code))
    result = repl.run(sandbox, object(), {"code": "1"}, on_call=lambda _c: {}, timeout=5, alive=lambda: True)
    assert result == {"stdout": "ok"} and seen == ["caller"]


def test_a_watched_execution_reraises_the_sdk_error_on_the_calling_thread(monkeypatch) -> None:
    monkeypatch.setattr(repl, "LIVENESS_POLL_SECONDS", 0.01)

    def run_code(*_args: Any, **_kwargs: Any) -> Any:
        raise DaytonaTimeoutError("Execution timed out")

    sandbox = SimpleNamespace(code_interpreter=SimpleNamespace(run_code=run_code))
    with pytest.raises(DaytonaTimeoutError):
        repl.run(sandbox, object(), {"code": "1"}, on_call=lambda _c: {}, timeout=5, alive=lambda: True)


def test_cancel_turn_stops_the_root_sandbox_without_holding_the_manager_lock() -> None:
    stopping = threading.Event()
    release = threading.Event()

    def stop(_sandbox: Any, **_kwargs: Any) -> None:
        stopping.set()
        assert release.wait(5)

    root = _root()
    root._client = SimpleNamespace(stop=stop)
    root._sandbox = SimpleNamespace(id="root-sandbox", state="started")
    root._contexts["main"] = object()
    worker = threading.Thread(target=root.cancel_turn, daemon=True)
    worker.start()
    try:
        assert stopping.wait(5)
        # While the provider is still stopping, another thread can take the lock and sees the cancellation.
        acquired = root._lock.acquire(timeout=1)
        assert acquired, "cancel_turn must not hold the manager lock across client.stop"
        root._lock.release()
        assert root._contexts == {}
        with pytest.raises(RuntimeError, match="cancelled"):
            root.ensure_sandbox()
        # A new Turn must not start remote work the in-flight stop would then kill: it waits for the stop.
        root.reset_turn_budget()
        ensured = threading.Event()
        follower = threading.Thread(target=lambda: root.ensure_sandbox() and ensured.set(), daemon=True)
        follower.start()
        assert not ensured.wait(0.3)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert ensured.wait(5)
    root.end_turn()
