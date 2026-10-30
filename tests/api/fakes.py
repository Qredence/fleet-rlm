"""In-memory Session components for API tests, wired in through FastAPI dependency overrides."""

from __future__ import annotations

import hashlib
import threading
import weakref
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from fleet_rlm.api.app import create_app
from fleet_rlm.api.deps import get_registry
from fleet_rlm.api.routes.turns import create_turn_executor
from fleet_rlm.config import FleetSettings
from fleet_rlm.session import SessionRuntime
from fleet_rlm.workspace import validate_session_id


class FakeManager(SimpleNamespace):
    def __init__(self, *, notes: list[dict[str, str]] | None = None, **overrides: Any) -> None:
        defaults: dict[str, Any] = {
            "event_callback": None,
            "_cancelled": threading.Event(),
            "_sandbox": None,
            "sub_lm": None,
            "max_llm_calls": 150,
            "max_depth": 3,
            "max_children": 8,
            "max_active_children": 4,
            "turn_timeout_seconds": 900,
            "turn_active": False,
            "snapshot_provenance": lambda: {},
            "deadline_provenance": lambda: {"deadline_source": "default", "effective_seconds": 900},
            "list_workspace_files": lambda: [],
            "read_workspace_file": lambda _path: b"",
            "write_learned_note": lambda *_args, **_kwargs: None,
            "reset_turn_budget": lambda: None,
            "end_turn": lambda: None,
            "cancel_turn": lambda: None,
        }
        super().__init__(**{**defaults, **overrides})
        if "request_cancellation" not in overrides:
            # Mirror the real manager's single-owner guard: the terminal branch, a disconnect, and
            # a deadline all may race, and only one of them may invoke cancel_turn().
            self._cancellation_claimed = False
            self._teardown_done = threading.Event()

            def request_cancellation() -> None:
                if self._cancellation_claimed:
                    self._teardown_done.wait()
                    return
                self._cancellation_claimed = True
                try:
                    self.cancel_turn()
                finally:
                    self._teardown_done.set()

            self.request_cancellation = request_cancellation
        if "reset_turn_budget" not in overrides:

            def reset_turn_budget(
                *, deadline_seconds: int | None = None, deadline_source: str = "configuration", **_kwargs: Any
            ) -> None:
                self._cancellation_claimed = False
                self._teardown_done.clear()
                self.turn_active = True
                self.deadline_provenance = lambda: {
                    "deadline_source": deadline_source,
                    "effective_seconds": deadline_seconds,
                }

            self.reset_turn_budget = reset_turn_budget
        if "stat_workspace_file" not in overrides:
            self.stat_workspace_file = self._stat_file
        if "turn_inputs" not in overrides:
            # Derived from the other fakes so tests that only fake listing, contents, or notes stay consistent.
            self.turn_inputs = lambda: {
                "digests": {
                    path: hashlib.sha256(self.read_workspace_file(path)).hexdigest()
                    for path in self.list_workspace_files()
                },
                "notes": notes or [],
                "environment": "",
            }

    def _stat_file(self, path: str) -> dict[str, Any]:
        content = self.read_workspace_file(path)
        return {"path": path, "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def store_with(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "history": lambda _session: [],
        "turn_status": lambda _session, _turn: None,
        "save_runtime_settings": lambda *_args: None,
        "finish_turn": lambda *_args: None,
        "commit_turn": lambda *_args, **_kwargs: None,
    }
    return SimpleNamespace(**{**defaults, **overrides})


class FakeRuntime(SessionRuntime):
    """Allow individual runtime policy methods to be replaced in boundary tests."""


def engine_with(root: str = "openai/system.ai.root", sub: str = "openai/system.ai.sub") -> SimpleNamespace:
    return SimpleNamespace(
        execution_mode="hybrid",
        root_lm=SimpleNamespace(model=root),
        sub_lm=SimpleNamespace(model=sub),
        compiled_path=None,
    )


def runtime_with(**parts: Any) -> SessionRuntime:
    engine = parts.pop("engine", engine_with())
    execution_mode = parts.pop("execution_mode", getattr(engine, "execution_mode", "hybrid"))
    engine.execution_mode = execution_mode
    real_policy = parts.pop("real_policy", False)
    policy = {
        name: parts.pop(name)
        for name in (
            "claim_turn",
            "memories",
            "load_context",
            "commit_turn",
            "turn_status",
            "save_memory",
            "delete_memory",
        )
        if name in parts
    }
    runtime = FakeRuntime(
        sandbox_mgr=parts.pop("sandbox_mgr", FakeManager()),
        store=parts.pop("store", store_with()),
        skill_mgr=parts.pop(
            "skill_mgr",
            SimpleNamespace(sync_local_skills_to_volume=lambda: 0, build_catalog_xml=lambda: "<available_skills />"),
        ),
        engine=engine,
        **parts,
    )
    if not real_policy:
        defaults = {
            "claim_turn": lambda *_args: None,
            "memories": lambda *_args, **_kwargs: [],
            "load_context": lambda _session: ("", ""),
            "turn_status": lambda _turn: None,
        }
        for name, method in (defaults | policy).items():
            setattr(runtime, name, method)
    return runtime


def completion_for(
    turn_record: dict[str, Any],
    artifacts: list[str] | None = None,
    *,
    replay_snapshot: tuple[str, bytes] | None = None,
) -> dict[str, Any]:
    """Use the real canonical writer for tests replacing the commit boundary."""
    runtime = runtime_with(sandbox_mgr=FakeManager(list_workspace_files=lambda: artifacts or []))
    return runtime.commit_turn("session-a", turn_record, replay_snapshot=replay_snapshot)


class FakeRegistry:
    def __init__(self, runtime: SessionRuntime, engine_for_models: Any = None) -> None:
        self.runtime = runtime
        self._engine_for_models = engine_for_models

    def get(self, session_id: str) -> SessionRuntime:
        validate_session_id(session_id)
        return self.runtime

    def engine_for_models(self, runtime: SessionRuntime, models: dict[str, str]) -> Any:
        return self._engine_for_models(runtime, models)

    def evict(self, session_id: str) -> bool:
        validate_session_id(session_id)
        return self._evicted.pop(session_id, False) if hasattr(self, "_evicted") else True

    def close(self) -> None:
        return None


def api_settings(**values: Any) -> FleetSettings:
    return FleetSettings(
        _env_file=None,
        fleet_llm_base_url="https://gateway.example.com/ai-gateway/mlflow/v1",
        databricks_token="test-token",
        **values,
    )


@contextmanager
def opened_client(*args: Any, **kwargs: Any):
    """Create and enter a fake-runtime client with its FastAPI lifespan active."""
    client, app = client_for(*args, **kwargs)
    with client:
        yield client, app


def client_for(
    runtime: SessionRuntime | None = None,
    *,
    settings: FleetSettings | None = None,
    store: Any = None,  # A SimpleNamespace fake, not a real Store instance.
    **registry: Any,
) -> tuple[TestClient, Any]:
    app = create_app(
        settings=settings or api_settings(),
        initialize_db=False,
        configure_observability=False,
        store=store,
    )
    if runtime is not None:
        fake = FakeRegistry(runtime, **registry)
        app.dependency_overrides[get_registry] = lambda: fake
    client = TestClient(app, raise_server_exceptions=False)
    # TestClient requests without a context manager do not run FastAPI lifespan. Give each
    # test app an explicit executor owner instead of relying on a hidden process-global fallback.
    executor = create_turn_executor()
    app.state.turn_executor = executor
    weakref.finalize(client, executor.shutdown, wait=False, cancel_futures=True)
    return client, app
