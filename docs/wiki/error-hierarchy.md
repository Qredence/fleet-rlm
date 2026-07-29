<!--
Source: .qoder/repowiki (Qoder-generated knowledge card)
Original YAML frontmatter:
  kind: error_handling
  name: Structured Domain Error Hierarchy with FastAPI Exception Handlers
  category: error_handling
  scope:
      - '**'
  source_files:
      - src/fleet_rlm/api/errors.py
      - src/fleet_rlm/app.py
      - src/fleet_rlm/daytona/errors.py
      - src/fleet_rlm/rlm/errors.py
      - src/fleet_rlm/artifacts/errors.py
      - src/fleet_rlm/files/errors.py
      - src/fleet_rlm/sessions/errors.py
      - src/fleet_rlm/skills/errors.py
      - src/fleet_rlm/api/routes/turns.py
      - src/fleet_rlm/rlm/outcome.py
-->


The Fleet RLM codebase uses a layered, domain-scoped error hierarchy combined with centralized FastAPI exception handlers to produce a stable, client-safe HTTP contract.

**System and approach**
- Each domain module (artifacts, files, sessions, skills, daytona, rlm) defines its own small class hierarchy rooted on `RuntimeError` or `ValueError`, keeping error types close to the logic that raises them.
- The API layer translates these domain errors into a uniform JSON `ErrorResponse` schema (`code` + `message`) via FastAPI `exception_handler`s registered in `api/errors.py` and installed once during app creation in `app.py`.
- Provider/SDK exceptions from Daytona are normalized through `daytona/errors.py` helpers (`map_provider_error`, `classify_provider_error`, `sanitize_provider_message`) so upstream failures never leak secrets or implementation details.
- Turn lifecycle and termination use typed terminal exceptions (`TurnCancelled`, `TurnTimeout`, `TurnNoProgress`, `TurnIntegrityFailure`) that carry a stable `status` string consumed by the SSE streaming layer.

**Key files and packages**
- `src/fleet_rlm/api/errors.py` — `ErrorResponse` model, `_STATUS_DEFAULTS`, detail-to-code mapping, and `install_error_handlers` for `HTTPException` and `RequestValidationError`.
- `src/fleet_rlm/app.py` — calls `install_error_handlers(app)` during FastAPI construction; central wiring point.
- `src/fleet_rlm/daytona/errors.py` — `DaytonaAdapterError`, `ProviderRequestError`, classification utilities, secret sanitization, and 404 detection.
- `src/fleet_rlm/rlm/errors.py` — `RLMConfigError` / `RLMModelBundleError` and the `TurnTerminalError` family used by the runner.
- `src/fleet_rlm/artifacts/errors.py`, `src/fleet_rlm/files/errors.py`, `src/fleet_rlm/sessions/errors.py`, `src/fleet_rlm/skills/errors.py` — per-domain base + specific error classes.
- Route files under `src/fleet_rlm/api/routes/*.py` — catch domain exceptions and raise mapped HTTP errors via a local `_http_error` helper.
- `src/fleet_rlm/chat/turn_lifecycle.py` — turn lifecycle exceptions (`TurnNotFoundError`, `TurnInProgressError`, `TurnIdempotencyMismatchError`, etc.) caught at the route boundary.
- `src/fleet_rlm/rlm/outcome.py` — immutable `RLMOutcome` with `terminal_status` and `public_error_message` consumed by the streaming projector.

**Architecture and conventions**
- **Domain-first exceptions**: Business logic raises domain-specific subclasses (e.g. `ArtifactNotFoundError`, `SessionAccessDenied`, `InvalidSkillSelectionError`). These are never sent directly to clients.
- **Route-level translation**: Each route wraps the call in a try/except block and converts domain errors into HTTP responses using status codes defined in `_STATUS_DEFAULTS` (400, 404, 409, 422, 503, 504) plus a detail→code map for common messages.
- **Centralized exception handlers**: `install_error_handlers` registers global handlers for `HTTPException` and `RequestValidationError`, ensuring every unhandled FastAPI error still returns the closed `ErrorResponse` shape. Validation errors on `/turns` with `skill_selections` are specially mapped to `invalid_skill_selection`.
- **Provider error normalization**: All external SDK/provider exceptions go through `map_provider_error` → `DaytonaAdapterError`/`ProviderRequestError`, with `sanitize_provider_message` stripping credentials and private paths before any logging or response.
- **Terminal turn errors**: The runner signals failure modes through `TurnTerminalError` subclasses carrying a `status` field; the SSE projector reads `public_error_message` from `RLMOutcome` to surface a user-friendly message without leaking internals.

**Conventions and constraints**
- Every public HTTP error body conforms to `{"code": str, "message": str}` as enforced by the `ErrorResponse` Pydantic model and the two global exception handlers.
- Status codes are bounded: 400 (invalid_request), 404 (not_found variants), 409 (turn_in_progress, idempotency_mismatch), 422 (invalid_request / invalid_skill_selection), 503 (turn_unavailable), 504 (turn_preparation_timeout).
- Detail strings like "session not found", "run not found", etc. are explicitly mapped to stable codes; unknown details fall back to `request_failed`.
- Provider error classification is restricted to the literal set `auth | quota | network | timeout | provider_5xx | request_validation | mount_mismatch | interpreter | unknown`, preventing ad-hoc categories from leaking downstream.
- Secret sanitization is mandatory for all provider error messages via `_SECRET_PATTERNS`; no raw credentials may appear in logs or responses.
- Successful outcomes cannot carry a `public_error_message`; the `RLMOutcome.__post_init__` enforces this invariant.