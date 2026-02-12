# FastAPI App for fleet-rlm — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the minimal `server/app.py` with a proper FastAPI project layout (routers, middleware, schemas, lifespan, Scalar docs) while preserving the existing OpenTUI WebSocket contract.

**Architecture:** A `src/fleet_rlm/server/` package restructured into FastAPI-idiomatic modules — `main.py` (app factory + lifespan), `routers/` (chat, health, tasks, ws), `schemas.py` (Pydantic request/response models), `deps.py` (dependency injection for agent/config), `middleware.py`. The CLI `serve-api` command continues to work but now imports from the new layout. The OpenTUI `tui/` frontend needs zero changes — the `/ws/chat` contract stays identical.

**Tech Stack:** FastAPI 0.128+, Pydantic v2, uvicorn, scalar-fastapi, dspy, Modal, existing fleet_rlm core.

---

## Overview of Changes

```
src/fleet_rlm/server/
├── __init__.py          # re-exports create_app, ServerRuntimeConfig
├── main.py              # create_app() factory, lifespan, Scalar docs mount
├── config.py            # ServerRuntimeConfig (extracted from app.py)
├── deps.py              # FastAPI Depends: get_agent, get_config, get_planner_lm
├── schemas.py           # Pydantic request/response models
├── middleware.py         # CORS + request-id middleware
├── routers/
│   ├── __init__.py
│   ├── health.py        # GET /health, GET /ready
│   ├── chat.py          # POST /chat (sync one-shot)
│   ├── ws.py            # WebSocket /ws/chat (streaming — current contract)
│   └── tasks.py         # POST /tasks/basic, POST /tasks/analyze, etc.
```

---

### Task 1: Install `fastapi[standard]` and Verify CLI

**Files:**
- Modify: `pyproject.toml` (server extras)

**Step 1: Add `fastapi[standard]` to server extras**

In `pyproject.toml`, change the server extra from `"fastapi>=0.115.0"` to `"fastapi[standard]>=0.115.0"`. This pulls in `fastapi-cli`, `httptools`, `uvloop`, etc.

```toml
server = [
    "fastapi[standard]>=0.115.0",
    "scalar-fastapi>=1.5.0",
    "websockets>=14.0",
    "httpx[socks]==0.28.1",
    "pydantic==2.12.5",
]
```

Also update the `full` extra to match.

**Step 2: Sync deps**

Run: `uv sync --extra dev --extra server`
Expected: Clean install, no conflicts.

**Step 3: Verify fastapi CLI works**

Run: `uv run fastapi --help`
Expected: Shows `fastapi dev`, `fastapi run` commands.

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add fastapi[standard] to server extras"
```

---

### Task 2: Extract `ServerRuntimeConfig` into `server/config.py`

**Files:**
- Create: `src/fleet_rlm/server/config.py`
- Modify: `src/fleet_rlm/server/app.py` (remove dataclass, import from config)
- Modify: `src/fleet_rlm/server/__init__.py`

**Step 1: Write the failing test**

```python
# tests/test_server_config.py
from fleet_rlm.server.config import ServerRuntimeConfig

def test_default_config():
    cfg = ServerRuntimeConfig()
    assert cfg.secret_name == "LITELLM"
    assert cfg.timeout == 900
    assert cfg.react_max_iters == 10
    assert cfg.volume_name is None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fleet_rlm.server.config'`

**Step 3: Create `server/config.py`**

```python
"""Server runtime configuration."""

from __future__ import annotations

from pydantic import BaseModel


class ServerRuntimeConfig(BaseModel):
    secret_name: str = "LITELLM"
    volume_name: str | None = None
    timeout: int = 900
    react_max_iters: int = 10
    rlm_max_iterations: int = 30
    rlm_max_llm_calls: int = 50
```

Note: migrate from `dataclass` to `pydantic.BaseModel` for consistency with other models.

**Step 4: Update `server/__init__.py`** to re-export from `config.py`.

**Step 5: Update `server/app.py`** — remove the `ServerRuntimeConfig` dataclass, import from `.config`.

**Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_server_config.py -v`
Expected: PASS

**Step 7: Run existing tests to ensure nothing broke**

Run: `uv run pytest -q`
Expected: All pass.

**Step 8: Commit**

```bash
git add src/fleet_rlm/server/config.py src/fleet_rlm/server/__init__.py src/fleet_rlm/server/app.py tests/test_server_config.py
git commit -m "refactor: extract ServerRuntimeConfig to server/config.py"
```

---

### Task 3: Create `server/schemas.py` — Pydantic Request/Response Models

**Files:**
- Create: `src/fleet_rlm/server/schemas.py`
- Test: `tests/test_server_schemas.py`

**Step 1: Write the failing test**

```python
# tests/test_server_schemas.py
from fleet_rlm.server.schemas import ChatRequest, ChatResponse, HealthResponse, TaskRequest

def test_chat_request_defaults():
    req = ChatRequest(message="hello")
    assert req.docs_path is None
    assert req.trace is False

def test_health_response():
    r = HealthResponse(ok=True)
    assert r.ok is True

def test_task_request_defaults():
    req = TaskRequest(task_type="basic", question="test")
    assert req.max_iterations == 15
```

**Step 2: Run test — expect FAIL**

Run: `uv run pytest tests/test_server_schemas.py -v`

**Step 3: Create `server/schemas.py`**

```python
"""Pydantic request/response schemas for the FastAPI server."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str
    docs_path: str | None = None
    trace: bool = False


class ChatResponse(BaseModel):
    assistant_response: str
    trajectory: dict[str, Any] | None = None
    history_turns: int = 0


class HealthResponse(BaseModel):
    ok: bool = True
    version: str = "0.3.3"


class ReadyResponse(BaseModel):
    ready: bool
    planner_configured: bool


class TaskRequest(BaseModel):
    task_type: Literal["basic", "architecture", "api_endpoints", "error_patterns", "long_context", "summarize", "custom_tool"]
    question: str = ""
    docs_path: str | None = None
    query: str = ""
    max_iterations: int = 15
    max_llm_calls: int = 30
    timeout: int = 600
    chars: int = 10000
    verbose: bool = True


class TaskResponse(BaseModel):
    ok: bool = True
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class WSMessage(BaseModel):
    type: Literal["message", "cancel"] = "message"
    content: str = ""
    docs_path: str | None = None
    trace: bool = False
```

**Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/test_server_schemas.py -v`

**Step 5: Commit**

```bash
git add src/fleet_rlm/server/schemas.py tests/test_server_schemas.py
git commit -m "feat: add Pydantic schemas for server API"
```

---

### Task 4: Create `server/deps.py` — Dependency Injection

**Files:**
- Create: `src/fleet_rlm/server/deps.py`
- Test: `tests/test_server_deps.py`

**Step 1: Write the failing test**

```python
# tests/test_server_deps.py
from fleet_rlm.server.deps import ServerState

def test_server_state_init():
    state = ServerState()
    assert state.planner_lm is None
    assert state.config is not None
```

**Step 2: Run — expect FAIL**

**Step 3: Create `server/deps.py`**

```python
"""FastAPI dependency injection helpers."""

from __future__ import annotations

from typing import Any

from .config import ServerRuntimeConfig


class ServerState:
    """Shared server state, set during lifespan."""

    def __init__(self) -> None:
        self.config = ServerRuntimeConfig()
        self.planner_lm: Any | None = None

    @property
    def is_ready(self) -> bool:
        return self.planner_lm is not None


server_state = ServerState()


def get_config() -> ServerRuntimeConfig:
    return server_state.config


def get_planner_lm() -> Any:
    return server_state.planner_lm
```

**Step 4: Run tests — PASS**

**Step 5: Commit**

```bash
git add src/fleet_rlm/server/deps.py tests/test_server_deps.py
git commit -m "feat: add server dependency injection module"
```

---

### Task 5: Create `server/middleware.py`

**Files:**
- Create: `src/fleet_rlm/server/middleware.py`

**Step 1: Create middleware module**

```python
"""Server middleware configuration."""

from __future__ import annotations

import uuid

from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response


def add_middlewares(app) -> None:
    """Register all middleware on the given FastAPI app."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_request_id(request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
```

**Step 2: Commit**

```bash
git add src/fleet_rlm/server/middleware.py
git commit -m "feat: add server middleware (CORS + request-id)"
```

---

### Task 6: Create Router — `server/routers/health.py`

**Files:**
- Create: `src/fleet_rlm/server/routers/__init__.py`
- Create: `src/fleet_rlm/server/routers/health.py`
- Test: `tests/test_router_health.py`

**Step 1: Write the failing test**

```python
# tests/test_router_health.py
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from fleet_rlm.server.main import create_app
    app = create_app()
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_ready_no_planner(client):
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["ready"] is False
```

**Step 2: Run — expect FAIL** (main.py doesn't exist yet, that's okay — this test drives Task 7)

**Step 3: Create `routers/__init__.py`** (empty)

**Step 4: Create `routers/health.py`**

```python
"""Health and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import server_state
from ..schemas import HealthResponse, ReadyResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse()


@router.get("/ready", response_model=ReadyResponse)
async def ready():
    return ReadyResponse(
        ready=server_state.is_ready,
        planner_configured=server_state.planner_lm is not None,
    )
```

**Step 5: Commit**

```bash
git add src/fleet_rlm/server/routers/
git commit -m "feat: add health router"
```

---

### Task 7: Create Router — `server/routers/chat.py` (POST /chat)

**Files:**
- Create: `src/fleet_rlm/server/routers/chat.py`

**Step 1: Create `routers/chat.py`**

Extract the `POST /chat` logic from `app.py` into a router. Use `Depends` for config.

```python
"""Synchronous chat endpoint."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from fleet_rlm import runners
from ..deps import get_config, get_planner_lm, ServerRuntimeConfig
from ..schemas import ChatRequest

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("")
async def chat(
    request: ChatRequest,
    config: ServerRuntimeConfig = Depends(get_config),
):
    planner_lm = get_planner_lm()
    if planner_lm is None:
        raise HTTPException(503, "Planner LM not configured")

    result = await asyncio.to_thread(
        runners.run_react_chat_once,
        message=request.message,
        docs_path=request.docs_path,
        react_max_iters=config.react_max_iters,
        rlm_max_iterations=config.rlm_max_iterations,
        rlm_max_llm_calls=config.rlm_max_llm_calls,
        timeout=config.timeout,
        secret_name=config.secret_name,
        volume_name=config.volume_name,
        include_trajectory=request.trace,
    )
    return result
```

**Step 2: Commit**

```bash
git add src/fleet_rlm/server/routers/chat.py
git commit -m "feat: add chat router"
```

---

### Task 8: Create Router — `server/routers/ws.py` (WebSocket)

**Files:**
- Create: `src/fleet_rlm/server/routers/ws.py`

**Step 1: Extract the existing `/ws/chat` handler from `app.py` into `routers/ws.py`**

This is a direct migration — the WebSocket protocol is unchanged so the OpenTUI frontend continues working.

**Step 2: Commit**

```bash
git add src/fleet_rlm/server/routers/ws.py
git commit -m "feat: add WebSocket chat router"
```

---

### Task 9: Create Router — `server/routers/tasks.py` (Runner Endpoints)

**Files:**
- Create: `src/fleet_rlm/server/routers/tasks.py`

**Step 1: Create `routers/tasks.py`**

Expose the existing `runners.py` functions as REST endpoints:

- `POST /tasks/basic` → `runners.run_basic`
- `POST /tasks/architecture` → `runners.run_architecture`
- `POST /tasks/long-context` → `runners.run_long_context`
- `POST /tasks/check-secret` → `runners.check_secret_presence`

Each wraps the sync runner in `asyncio.to_thread`.

**Step 2: Commit**

```bash
git add src/fleet_rlm/server/routers/tasks.py
git commit -m "feat: add task runner endpoints"
```

---

### Task 10: Create `server/main.py` — App Factory + Lifespan + Scalar Docs

**Files:**
- Create: `src/fleet_rlm/server/main.py`
- Modify: `src/fleet_rlm/server/__init__.py`
- Modify: `src/fleet_rlm/cli.py` (update import path)

**Step 1: Create `server/main.py`**

```python
"""FastAPI application factory with lifespan and Scalar docs."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from fleet_rlm.config import get_planner_lm_from_env

from .config import ServerRuntimeConfig
from .deps import server_state
from .middleware import add_middlewares
from .routers import chat, health, tasks, ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    server_state.planner_lm = get_planner_lm_from_env()
    yield
    server_state.planner_lm = None


def create_app(*, config: ServerRuntimeConfig | None = None) -> FastAPI:
    cfg = config or ServerRuntimeConfig()
    server_state.config = cfg

    app = FastAPI(
        title="fleet-rlm",
        version="0.3.3",
        lifespan=lifespan,
    )

    add_middlewares(app)

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(ws.router)
    app.include_router(tasks.router)

    # Scalar API docs at /docs
    try:
        from scalar_fastapi import get_scalar_api_reference
        @app.get("/scalar", include_in_schema=False)
        async def scalar_docs():
            return get_scalar_api_reference(
                openapi_url=app.openapi_url,
                title=app.title,
            )
    except ImportError:
        pass

    return app


app = create_app()
```

The module-level `app = create_app()` lets `fastapi dev src/fleet_rlm/server/main.py` work out of the box.

**Step 2: Update `server/__init__.py`** to re-export from `main.py`.

**Step 3: Update `cli.py`** — change `from .server.app import` to `from .server.main import`.

**Step 4: Run the health test from Task 6**

Run: `uv run pytest tests/test_router_health.py -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `uv run pytest -q`
Expected: All pass.

**Step 6: Verify `fastapi dev` works**

Run: `uv run fastapi dev src/fleet_rlm/server/main.py --port 8000`
Expected: Server starts, http://localhost:8000/docs shows Swagger, http://localhost:8000/health returns `{"ok": true}`.

**Step 7: Commit**

```bash
git add src/fleet_rlm/server/ src/fleet_rlm/cli.py tests/
git commit -m "feat: FastAPI app factory with lifespan, routers, Scalar docs"
```

---

### Task 11: Delete Legacy `server/app.py`

**Files:**
- Delete: `src/fleet_rlm/server/app.py`

**Step 1: Remove `app.py`** — all code has been migrated to `main.py`, routers, schemas, deps.

**Step 2: Grep for any remaining imports of `server.app`**

Run: `rg "server.app" src/ tests/`
Expected: No matches.

**Step 3: Run full test suite**

Run: `uv run pytest -q`

**Step 4: Commit**

```bash
git rm src/fleet_rlm/server/app.py
git commit -m "chore: remove legacy server/app.py"
```

---

### Task 12: Update AGENTS.md + README

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`

**Step 1: Add new serve commands to AGENTS.md**

```
uv run fastapi dev src/fleet_rlm/server/main.py   # Dev server with hot reload
uv run fleet-rlm serve-api                         # Production server via CLI
```

**Step 2: Update README** with the new API structure and Scalar docs URL.

**Step 3: Commit**

```bash
git add AGENTS.md README.md
git commit -m "docs: update docs for new FastAPI server layout"
```

---

## Final Structure

```
src/fleet_rlm/server/
├── __init__.py          # re-exports create_app, ServerRuntimeConfig
├── main.py              # app factory, lifespan, Scalar mount, module-level `app`
├── config.py            # ServerRuntimeConfig (Pydantic BaseModel)
├── deps.py              # ServerState singleton, Depends helpers
├── schemas.py           # ChatRequest, ChatResponse, TaskRequest, etc.
├── middleware.py         # CORS + request-id
└── routers/
    ├── __init__.py
    ├── health.py        # GET /health, GET /ready
    ├── chat.py          # POST /chat
    ├── ws.py            # WebSocket /ws/chat (identical protocol)
    └── tasks.py         # POST /tasks/{type}
```

## Key Invariants

1. **OpenTUI contract unchanged** — `/ws/chat` protocol stays identical
2. **`fleet-rlm serve-api` still works** — just imports from new location
3. **`fastapi dev` works** — `main.py` has module-level `app = create_app()`
4. **No new mandatory deps** — all new code is behind `[server]` extra
