"""In-memory Session components for API tests, wired in through FastAPI dependency overrides."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from fleet_rlm.api.app import create_app
from fleet_rlm.api.deps import get_registry
from fleet_rlm.config import FleetSettings
from fleet_rlm.session import SessionRuntime
from fleet_rlm.workspace import validate_session_id


class FakeManager(SimpleNamespace):
    def __init__(self, **overrides: Any) -> None:
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
            "list_workspace_files": lambda: [],
            "read_workspace_file": lambda _path: b"",
            "list_learned_notes": lambda: [],
            "write_learned_note": lambda *_args, **_kwargs: None,
            "reset_turn_budget": lambda: None,
            "end_turn": lambda: None,
            "cancel_turn": lambda: None,
        }
        super().__init__(**{**defaults, **overrides})


def store_with(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "history": lambda _session: [],
        "save_runtime_settings": lambda *_args: None,
        "finish_turn": lambda *_args: None,
    }
    return SimpleNamespace(**{**defaults, **overrides})


class FakeHarness(SimpleNamespace):
    def __init__(self, **overrides: Any) -> None:
        defaults: dict[str, Any] = {
            "claim_turn": lambda *_args: None,
            "memories": lambda *_args, **_kwargs: [],
            "load_context": lambda _session: ("", ""),
            "commit_turn": lambda **_kwargs: [],
            "turn_status": lambda _turn_id: None,
            "store": store_with(),
        }
        super().__init__(**{**defaults, **overrides})


def engine_with(root: str = "openai/system.ai.root", sub: str = "openai/system.ai.sub") -> SimpleNamespace:
    return SimpleNamespace(
        execution_mode="hybrid",
        root_lm=SimpleNamespace(model=root),
        sub_lm=SimpleNamespace(model=sub),
        compiled_path=None,
    )


def runtime_with(**parts: Any) -> SessionRuntime:
    return SessionRuntime(
        sandbox_mgr=parts.pop("sandbox_mgr", FakeManager()),
        harness=parts.pop("harness", FakeHarness()),
        skill_mgr=parts.pop(
            "skill_mgr",
            SimpleNamespace(sync_local_skills_to_volume=lambda: 0, build_catalog_xml=lambda: "<available_skills />"),
        ),
        engine=parts.pop("engine", engine_with()),
        **parts,
    )


class FakeRegistry:
    def __init__(self, runtime: SessionRuntime, engine_for_models: Any = None) -> None:
        self.runtime = runtime
        self._engine_for_models = engine_for_models

    def get(self, session_id: str) -> SessionRuntime:
        validate_session_id(session_id)
        return self.runtime

    def engine_for_models(self, runtime: SessionRuntime, models: dict[str, str]) -> Any:
        return self._engine_for_models(runtime, models)

    def close(self) -> None:
        return None


def api_settings(**values: Any) -> FleetSettings:
    return FleetSettings(
        _env_file=None,
        fleet_llm_base_url="https://gateway.example.com/ai-gateway/mlflow/v1",
        databricks_token="test-token",
        **values,
    )


def client_for(
    runtime: SessionRuntime | None = None, *, settings: FleetSettings | None = None, **registry: Any
) -> tuple[TestClient, Any]:
    app = create_app(settings=settings or api_settings(), initialize_db=False, configure_observability=False)
    if runtime is not None:
        fake = FakeRegistry(runtime, **registry)
        app.dependency_overrides[get_registry] = lambda: fake
    return TestClient(app, raise_server_exceptions=False), app
