# Fleet RLM

Fleet RLM is an RLM-native backend built around FastAPI SSE, `dspy.RLM`, and
Daytona Sandboxes with workspace-scoped durable Volumes.

The canonical backend is `src/fleet_rlm/`. The former compatibility backend has
been removed. The existing React frontend has not yet been adapted to the new
SSE contract and is intentionally outside the backend cutover.

## Install and run

```bash
uv sync --all-extras --dev
# combined backend + Ink terminal
uv run fleet cli   # Daytona
uv run fleet deno  # Deno/Pyodide
# backend-only launchers
uv run fleet web
uv run fleet-rlm serve-api
```

`fleet cli` and `fleet deno` require Node 22+ and pnpm. They wait for backend
readiness, keep backend output in `.fleet_rlm/logs/latest.log`, and stop the
backend when Ink exits. Forward Ink arguments after `--`, for example
`uv run fleet deno -- --session <uuid>`. Before a Daytona run, the explicit
`uv run fleet doctor daytona` check validates database/provider access and a
disposable scoped Sandbox without creating Fleet rows.

Daytona is the default runtime. For reduced local compatibility development,
use Deno with SQLite and a host-only BYOK provider key:

```bash
export FLEET_RUN_ENVIRONMENT=deno
export FLEET_DATABASE_URL='sqlite+aiosqlite:///./.fleet_rlm/local.sqlite3'
export FLEET_LLM_API_KEY='...'
uv run fleet-rlm serve-api
```

Use PostgreSQL only for an explicitly authorized disposable/live lane:

```bash
export FLEET_RUN_ENVIRONMENT=daytona
export FLEET_DATABASE_URL='postgresql+asyncpg://...'
export FLEET_DAYTONA_API_KEY='...'
export FLEET_LLM_API_KEY='...'
uv run python scripts/db_init.py
uv run fleet-rlm serve-api
```

## Backend API

- `POST /api/sessions/{session_id}/turns` — local-scope, idempotent SSE Turn execution.
- `/api/sessions` — Session CRUD and ordered Turn history.
- `/api/attachments` — durable Attachment upload and metadata lookup.
- `GET /api/artifacts/{artifact_id}` — committed Artifact retrieval.
- `/api/skills` — authorized Skill Card discovery.
- `PUT /api/runs/{run_id}/cancellation` — durable, authorized Run cancellation.

There is no `/api/v1`, WebSocket execution, optimization/evaluation API,
runtime-admin API, Volume browser, BYOK profile API, or public Artifact creation.
See [HTTP API](docs/reference/http-api.md) and generated [OpenAPI](openapi.yaml).

## Architecture

One Turn applies the deterministic local scope and validates Attachment references before opening SSE,
restores durable Session History, creates a fresh `dspy.RLM`, executes
host-mediated tools, commits the Turn transactionally, and emits exactly one
terminal Runtime Event. Daytona Turns additionally acquire a Sandbox
Interpreter Lease and promote private Artifact Candidates before commit; Deno
Turns use DSPy's local Deno/Pyodide interpreter and do not promote Artifacts.

Read [architecture](docs/architecture.md), the [backend context](src/fleet_rlm/CONTEXT.md),
and the [codebase map](docs/reference/codebase-map.md).

## Database

Production schema evolution is owned by Alembic and starts from one fresh
canonical baseline. Deno SQLite is initialized by the local app; use the
following only for a disposable PostgreSQL database:

```bash
export FLEET_DATABASE_URL='postgresql+asyncpg://...'
uv run alembic upgrade head
uv run alembic check
```

Runtime live startup never calls SQLAlchemy `create_all`.

## Validation

```bash
make check
FLEET_LIVE=1 uv run pytest tests/live/backend/test_b5_attachment_artifact_durability.py -q
```

`make api-sync` regenerates only root `openapi.yaml`; frontend generated
contracts remain deferred to the frontend adaptation effort.
