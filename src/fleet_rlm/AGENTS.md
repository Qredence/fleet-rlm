# Backend Agent Guide: `src/fleet_rlm/`

Backend, runtime, API, persistence, Daytona, and package rules for the
`fleet-rlm` FastAPI application. This guide is for developers working in
`src/fleet_rlm/`; the root `AGENTS.md` provides the top-level map.

**Important:** this is a reference for *backend code only* — frontend rules
live in `src/frontend/AGENTS.md`.

---

## Source Layout

```
src/fleet_rlm/
├── __init__.py           # Package version
├── api/                  # FastAPI transport layer
│   ├── main.py           # Application factory, route registration
│   ├── config.py         # AppConfig, runtime configuration
│   ├── dependencies.py   # FastAPI dependency injection (require_http_identity, etc.)
│   ├── bootstrap.py      # Server state lifecycle
│   ├── errors.py         # Exception handlers
│   ├── middleware.py      # FastAPI middleware
│   ├── auth/             # Authentication (NormalizedIdentity, Neon, Entra)
│   ├── events/           # Event projectors (project_chat, project_sse)
│   ├── routers/          # FastAPI route handlers
│   ├── runtime_services/ # Transport-neutral runtime services
│   └── schemas/          # Pydantic request/response models
├── cli/                  # CLI commands (fleet, fleet-rlm)
├── integrations/         # External service integrations (Daytona, local_store, etc.)
├── quality/              # Quality analysis modules (GEPA, etc.)
├── runtime/              # DSPy runtime layer
│   ├── events.py         # RuntimeEvent, RuntimeEventKind (14 kinds)
│   ├── agent/            # AgentRuntime, RLM wrappers
│   ├── modules/          # EscalatingFleetModule, factory.py
│   ├── tools/            # Tool registry (discover_tools)
│   └── sandbox_types.py  # SandboxSerializable types
├── ui/                   # Built UI artifacts (served by FastAPI)
└── utils/                # Shared utilities
```

---

## API Routes

### REST / Admin Routes (under `/api/v1/`)

Registered in `api/routers/_composition.py` (now in `api/main.py` via
`_register_api_routes()`). Prefix: ``/api/v1``.

| Route group | File | Purpose |
|---|---|---|
| ``/api/v1/auth/*`` | ``api/routers/auth.py`` | Authentication endpoints |
| ``/api/v1/info/*`` | ``api/routers/info.py`` | Server info |
| ``/api/v1/ws/*`` | ``api/routers/ws/`` | WebSocket execution and events |
| ``/api/v1/sessions/*`` | ``api/routers/sessions.py`` | Session CRUD |
| ``/api/v1/runtime/*`` | ``api/routers/runtime.py`` | Runtime settings, profiles, status |
| ``/api/v1/llm-profiles/*`` | ``api/routers/llm_profiles.py`` | LLM provider profiles |
| ``/api/v1/sandboxes/*`` | ``api/routers/sandboxes.py`` | Daytona sandbox browsing |
| ``/api/v1/runs/*`` | ``api/routers/runs.py`` | Run lifecycle |
| ``/api/v1/optimization/*`` | ``api/routers/optimization.py`` | GEPA optimization |
| ``/api/v1/traces/*`` | ``api/routers/traces.py`` | MLflow traces |

### Chat SSE Endpoint (app root, NOT under `/api/v1/`)

Mounted at app root via ``app.include_router(chat.router)`` in
``api/main.py``, **after** the ``api_v1`` block so it cannot shadow existing
``/api/v1/*`` routes.

| Route | File | Purpose |
|---|---|---|
| ``POST /api/chat`` | ``api/routers/chat.py`` | AI SDK UIMessage v1 SSE streaming chat |

The chat router uses ``APIRouter(prefix="/api/chat", tags=["chat"])``; the
endpoint is ``/api/chat`` (not ``/api/v1/chat``). This matches the AI SDK
``useChat`` default path convention.

**SSE protocol** (see `ADR-0003 <../../docs/adr/0003-api-chat-ai-sdk-uimessage-stream.md>`_):

- ``Content-Type: text/event-stream``
- ``x-vercel-ai-ui-message-stream: v1``
- Each part: ``data: {json}\n\n``
- Termination: ``data: [DONE]\n\n``

**Request body** (``ChatRequest`` in ``api/schemas/chat.py``):

- ``messages: list[ChatMessage]`` (``min_length=1``)
- ``ChatMessage``: ``role Literal["user","assistant","system","tool"]``,
  ``content: str | None``, ``parts: list[dict] | None``
- Fleet control fields: ``session_id``, ``execution_mode``, ``repo_url``,
  ``repo_ref``, ``context_paths``, ``batch_concurrency``, ``docs_path``,
  ``trace``, ``trace_mode``, ``selected_skill_ids``
- Uses ``extra="forbid"`` (matching ``WSMessage`` policy)
- Legacy ``execution_mode`` values (``auto``/``rlm_only``/``tools_only``)
  accepted without error (collapsed in Phase 2)

**Auth:** ``require_http_identity`` (HTTPBearer → ``NormalizedIdentity``).
Local-mode bypass: ``auth_required=false`` returns
``build_unauthenticated_identity(cfg)``.

**Cancellation:** ``await request.is_disconnected()`` flips
``cancel_flag["cancelled"]``; runtime ``cancel_check`` polls the same dict.

---

## Transport / Runtime Seam

The transport-neutral seam lives in ``api/runtime_services/``. Both the SSE
endpoint and the WebSocket endpoint share it.

### ChatExecutionContext + TurnControls

Defined in ``api/runtime_services/chat_context.py``. No WebSocket/Request
imports. No import-time side effects.

```python
@dataclass(slots=True)
class TurnControls:
    execution_mode: str | None = None
    repo_url: str | None = None
    repo_ref: str | None = None
    context_paths: list[str] = field(default_factory=list)
    batch_concurrency: int | None = None
    docs_path: str | None = None
    trace: bool | None = None
    trace_mode: str | None = None
    selected_skill_ids: list[str] = field(default_factory=list)

@dataclass(slots=True)
class ChatExecutionContext:
    prepared: PreparedChatRuntime
    identity: NormalizedIdentity
    session_id: str | None
    canonical_workspace_id: str | None
    canonical_user_id: str | None
    owner_tenant_claim: str | None
    owner_user_claim: str | None
    cancel_flag: dict[str, bool]
    controls: TurnControls
```

See `ADR-0004 <../../docs/adr/0004-chat-execution-context-seam.md>`_ for the
full design rationale.

### stream_turn()

Defined in ``api/runtime_services/stream_turn.py``. Transport-neutral async
generator:

```python
async def stream_turn(
    ctx: ChatExecutionContext,
    message: str,
) -> AsyncIterator[RuntimeEvent]:
```

- Delegates to ``AgentRuntime.aiter_chat_turn_stream()`` with a
  ``cancel_check`` reading ``ctx.cancel_flag.get("cancelled", False)``
- Threads non-``None`` ``TurnControls`` fields as kwargs
- Calls ``agent.set_execution_mode()`` when
  ``ctx.controls.execution_mode`` is not ``None``
- No ``WebSocket``/``Request`` imports
- Session restoration via ``ctx.session_id``

### stream_turn() — threading diagram

```
POST /api/chat (SSE) ─┐
WS /api/v1/ws/exec ───┤──> ChatExecutionContext ──> stream_turn() ──> AgentRuntime
                      │                              │                  .aiter_chat_turn_stream()
                      ├── WS:  project_chat()        │
                      └── SSE: project_sse()         └── AsyncIterator[RuntimeEvent]
```

---

## Event Projectors

### project_chat (WebSocket, UNCHANGED)

- File: ``api/events/project_chat.py``
- Projects ``RuntimeEvent`` → WS frames (schema version 3)
- DO NOT MODIFY in Phase 1 (sacred; WebSocket projection is stable)

### project_sse (SSE, NEW)

- File: ``api/events/project_sse.py``
- Projects ``RuntimeEvent`` → AI SDK UIMessage v1 SSE ``data:`` lines
- Maps all 14 ``RuntimeEventKind`` values (none silently dropped):

| Kind | AI SDK v1 part(s) |
|---|---|
| ``TEXT`` | ``text-start`` / ``text-delta`` / ``text-end`` |
| ``REASONING`` | ``reasoning-start`` / ``reasoning-delta`` / ``reasoning-end`` |
| ``TOOL_CALL`` | ``tool-input-start`` / ``tool-input-available`` |
| ``TOOL_RESULT`` | ``tool-output-available`` |
| ``TURN_STARTED`` | ``start`` (messageId) / ``start-step`` / ``data-agent`` |
| ``TURN_INPUTS`` | ``data-turn-inputs`` |
| ``SANDBOX_EXEC`` | ``data-sandbox-exec`` |
| ``RLM_DELEGATE`` | ``data-rlm-delegate`` |
| ``MLFLOW_SPAN`` | ``data-span`` |
| ``STATUS`` | ``data-status`` |
| ``WARNING`` | ``data-warning`` |
| ``CLARIFICATION`` | ``data-clarification`` |
| ``DONE`` | ``finish-step`` / ``finish`` / ``[DONE]`` |
| ``ERROR`` | ``error`` / ``[DONE]`` |
| cancel | ``abort`` / ``[DONE]`` |

Additional ``data-*`` parts from payload fields: ``data-artifact``,
``data-task``, ``data-performance``, ``data-suggestion`` (emitted alongside
primary mapping, never suppressing it).

- ``TEXT``/``REASONING``: start/delta/end wrappers, not bare deltas
- No ``tool-input-delta`` in Phase 1
- Terminal events always followed by ``data: [DONE]\n\n``

---

## Foundation Principle (Binding)

| Layer | Owns | Does NOT own |
|---|---|---|
| **FastAPI** | auth, runtime settings, LLM profiles, sessions, trace/debug APIs, SSE transport | reasoning logic, RLM/tool/skill decisions |
| **DSPy** | ``dspy.RLM`` execution, ``dspy.Tool`` wrapping, ``dspy.Signature`` contracts | transport, persistence substrate |
| **Daytona** | sandbox execution, durable volume filesystem | transport, reasoning |

**Prohibitions:**
- Do NOT replace DSPy with hand-written orchestration
- Do NOT make FastAPI handlers own reasoning logic
- Do NOT move RLM/tool/skill decision logic into the frontend
- Do NOT use SSE as a reason to bypass DSPy modules, signatures, tools, or
  ``SandboxSerializable`` inputs

---

## Auth

- ``require_http_identity`` (``api/dependencies.py``) → ``NormalizedIdentity``
  via ``HTTPBearer(auto_error=False)``
- Local-mode bypass: ``auth_required=False`` returns
  ``build_unauthenticated_identity(cfg)``
- WS auth: ``_authenticate_websocket()`` produces the same
  ``NormalizedIdentity``
- SSE auth: ``require_http_identity`` (HTTPBearer → ``NormalizedIdentity``)
- ``HTTPIdentityDep = Annotated[NormalizedIdentity, Depends(require_http_identity)]``
- Suports Neon, Entra, and ``dev`` auth modes

---

## Runtime

The DSPy runtime layer lives in ``runtime/`` and is **off-limits for Phase 1
changes**:

- ``runtime/events.py`` — ``RuntimeEvent`` + ``RuntimeEventKind`` (14 kinds) +
  ``RuntimeToolInfo`` + ``RuntimeActorContext``
- ``runtime/agent/runtime.py`` — ``AgentRuntime.aiter_chat_turn_stream()``
- ``runtime/modules/*`` — ``EscalatingFleetModule``, ``factory.py``
- ``runtime/tools/registry.py`` — ``discover_tools()``
- ``runtime/sandbox_types.py`` — ``SandboxSerializable`` types

---

## Persistence

- Local SQLite fallback: ``integrations/local_store.py`` (when
  ``DATABASE_URL`` unset)
- Neon Postgres: production, requires ``DATABASE_URL`` and
  ``DATABASE_REQUIRED=true``
- Alembic migrations in ``migrations/``; check drift with ``alembic check``
- Session persistence via ``get_session_record`` / ``store_session_record``

---

## Daytona

Daytona modules in ``integrations/daytona/`` are **off-limits for Phase 1
changes**. The Daytona interpreter provides sandbox execution, volume
filesystem, workspace staging, and skill/resource access.

---

## Package Rules

- Use ``uv run`` for all Python commands (never raw ``python3``/``python``)
- Use ``pydantic v2`` models with ``ConfigDict(extra="forbid")``
- Use ``@dataclass(slots=True)`` for new context/control dataclasses
- Async generators for streaming functions
- Type hints throughout; ``make typecheck`` (ty check) must pass
- Follow ruff format + ruff check conventions (``make format``, ``make lint``)

---

## Generated Artifacts (Do Not Hand-Edit)

- ``openapi.yaml``
- ``src/frontend/src/lib/rlm-api/generated/openapi.ts``
- ``src/frontend/openapi/fleet-rlm.openapi.yaml``
- ``src/frontend/src/routeTree.gen.ts``
- ``src/frontend/dist/``
- ``src/fleet_rlm/ui/dist/``

Use: ``make api-sync``, ``make api-check``, ``make build-ui``.

---

## Validation

```bash
make format-check && make lint && make typecheck && make test && make api-check
```

Scoped test commands for the transport area:

```bash
# SSE + context + projector unit tests
uv run --no-sync pytest -q tests/unit/api tests/unit/runtime_services tests/unit/events -m "not live_llm and not live_daytona and not benchmark and not db"

# WS regression (must pass unchanged)
uv run --no-sync pytest -q tests/unit/api/test_ws_stream_events.py tests/unit/api/test_ws_turn_runner.py tests/unit/api/ws -m "not live_llm and not live_daytona and not benchmark and not db"
```
