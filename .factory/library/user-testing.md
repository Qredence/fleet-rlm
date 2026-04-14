# User Testing

## Validation Surface

### Workspace shell and live execution
- Tool: `agent-browser`
- Entry: `http://127.0.0.1:8000/app/workspace` (API-served) or `http://127.0.0.1:5173/app/workspace` (dev server)
- Verify: shell loads, sidebar conversation list renders, runtime warning flows work, trace/reasoning cards render from streamed events

### Session History surface
- Tool: `agent-browser`
- Entry: `http://127.0.0.1:5173/app/history` (dev server required for this route)
- Verify: `/app/history` route loads, session list renders sessions from backend API, search/filter controls work, clicking a session shows session detail with turn-by-turn transcript, delete action removes entry

### Runtime diagnostics and settings
- Tools: `agent-browser`, `curl`, focused `pytest`
- Verify: `/health`, `/ready`, `/api/v1/runtime/status`, settings snapshot/load-save behavior, and LM/Daytona smoke-test controls/results

### Optimization dashboard
- Tools: `agent-browser`, focused `pytest`, `curl`
- Entry: `http://127.0.0.1:5173/app/optimization`
- Verify: 4-tab dashboard renders (Modules, Datasets, Runs, Compare), module cards show correct slugs, dataset upload form works, run list includes new runs, run detail shows per-example scores when available

### Sessions REST API
- Tool: `curl`, focused `pytest`
- Endpoints: `GET /api/v1/sessions`, `GET /api/v1/sessions/{id}`, `GET /api/v1/sessions/{id}/turns`, `DELETE /api/v1/sessions/{id}`
- Verify: list returns paginated sessions, detail returns turns, delete removes the session

### Optimization REST API — new endpoints
- Tool: `curl`, focused `pytest`
- Endpoints: `POST /api/v1/optimization/datasets`, `GET /api/v1/optimization/datasets`, `GET /api/v1/optimization/runs/{id}/results`
- Verify: dataset registration, list, and run result retrieval respond with correct shapes

### Shared backend contract
- Tools: focused `pytest`
- Verify: websocket execution envelope, bridge retirement did not change stream behavior, session persistence works end-to-end, optimization/evaluation entrypoint compatibility

## Validation Readiness Dry Run

Dry run completed at mission start (product completion mission).

Observed results:
- `GET /health` → 200 OK (`{"ok":true,"version":"0.5.0"}`)
- `GET /ready` → 200 OK (`{"ready":true,"planner":"ready","database":"ready","sandbox_provider":"daytona"}`)
- `GET /api/v1/auth/me` → 200 OK (`dev` mode, anonymous user)
- `GET /api/v1/optimization/status` → 200 OK (`{"available":true,"mlflow_enabled":true,"gepa_installed":true}`)
- `GET /api/v1/sessions/state` → 200 OK (empty sessions list)
- Frontend dev server on port 5173 started and served HTML successfully
- Playwright 1.59.1 available
- 872 unit tests pass in ~16.5 seconds

No blockers found. Full validation path is executable.

## Validation Concurrency

Machine profile:
- CPU cores: 10
- Memory: 16 GiB
- Current usage at planning: ~12.7 GB used — moderate-to-high memory pressure
- Note: MLflow, Playwright MCP, IDE, and many node/python helpers already running

### agent-browser (frontend surfaces)
- Max concurrent validators: **2**
- Rationale: Each Chromium instance uses ~300–400 MB RAM + dev server ~200 MB. With ~3.3 GB headroom × 0.7 = ~2.3 GB budget. 2 validators = ~900 MB (fits safely). 3 validators risk instability under existing memory pressure.

### curl / REST API checks
- Max concurrent validators: **4**
- Rationale: No browser involved; pure HTTP. Very low memory per call. The API server is shared but handles concurrent requests fine.

### Focused pytest lanes
- Max concurrent: **2** (serialized by default for websocket/session tests that share SQLite state)
- Rationale: SQLite local_store has no row-level locking; concurrent writes risk corruption. Serialize by default; parallelize only read-only contracts.
