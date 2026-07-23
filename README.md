# Fleet RLM

Fleet RLM is an RLM-native backend built around FastAPI SSE, `dspy.RLM`, and
Daytona Sandboxes with workspace-scoped durable Volumes. The canonical backend
is `src/fleet_rlm/`; the maintained development client is the pi-tui workspace
under `tools/fleet-tui/`.

## Install and run

```bash
uv sync --all-extras --dev

# Select the required non-secret runtime policy profile.
export FLEET_CONFIG_PROFILE=daytona

# supervised backend + pi-tui
uv run fleet cli   # Daytona
uv run fleet deno  # Deno/Pyodide

# backend only
uv run fleet web
uv run fleet-rlm serve-api --port 8000
```

`fleet cli` and `fleet deno` require Node 22.19+ and pnpm. They wait for backend
readiness, write backend output under `.fleet_rlm/logs/`, and stop the backend
when pi-tui exits. Forward terminal arguments after `--`, for example:

```bash
uv run fleet deno -- --session <uuid>
```

The terminal uses native scrollback and does not own a model, provider key, or
Sandbox. See the [terminal guide](docs/how-to-guides/terminal-tui.md) and
[configuration reference](docs/reference/configuration.md).

### Deno

Deno is the canonical reduced local runtime. It needs an LLM key and Deno on
`PATH`; SQLite is the normal local persistence choice.

```bash
export FLEET_CONFIG_PROFILE=local-deno
export FLEET_DATABASE_URL='sqlite+aiosqlite:///./.fleet_rlm/local.sqlite3'
export FLEET_LLM_API_KEY='...'
uv run fleet-rlm serve-api --port 8000
```

Deno uses a real LM and DSPy's default Deno/Pyodide interpreter. It supports
Attachment reads and Skills but not Daytona resources or durable Artifact
promotion.

### Daytona

Daytona is the full Fleet runtime and requires an LLM key, Daytona key, and
database URL. Initialize the configured database explicitly; startup never
applies migrations automatically.

```bash
export FLEET_CONFIG_PROFILE=daytona
export FLEET_DATABASE_URL='postgresql+asyncpg://...'
export FLEET_DAYTONA_API_KEY='...'
export FLEET_LLM_API_KEY='...'
uv run python scripts/daytona_snapshot.py check --name fleet-rlm-python313-v2
uv run python scripts/db_init.py
uv run fleet-rlm serve-api --port 8000
```

Use `uv run fleet doctor daytona` for an opt-in disposable provider, database,
mount, and interpreter probe before diagnosing a Turn.

## Backend API

- `POST /api/sessions/{session_id}/turns` — local-scope idempotent SSE execution.
- `/api/sessions` — Session CRUD and ordered committed Turn history.
- `/api/attachments` — durable Attachment upload and metadata lookup.
- `/api/artifacts/{artifact_id}` — committed metadata and verified content.
- `/api/skills` — bounded system Skill Card discovery for the four bundled
  Skills.
- `PUT /api/runs/{run_id}/cancellation` — durable Run cancellation.

There is no `/api/v1`, WebSocket execution, optimization/evaluation API,
runtime-admin API, Volume browser, BYOK profile API, or public Artifact creation.
See the [HTTP API](docs/reference/http-api.md) and generated
[OpenAPI](openapi.yaml).

## Architecture

One Turn validates its deterministic local scope, Attachments, and exact Skill
selections before opening SSE. `TurnCoordinator` begins and prepares execution,
`RLMRunner` runs one fresh native `dspy.RLM`, and `TurnLifecycle.finish()` owns
result snapshot handling, Artifact publication, and atomic Turn Commit. The
coordinator then projects the terminal suffix and cleans up Run resources.

Daytona Turns acquire a fresh Interpreter Lease and use Workspace Volume Scope.
Deno Turns use DSPy's local Deno/Pyodide interpreter and skip durable Artifact
promotion. Full Session history stays host-side behind the bounded
`read_session_history` Tool.

Read the [architecture](docs/architecture.md), [backend context](src/fleet_rlm/CONTEXT.md),
and [codebase map](docs/reference/codebase-map.md).

## Database

Alembic owns live schema evolution through one canonical baseline. Deno SQLite
may be initialized by the local app; for an explicit disposable/live database:

```bash
uv run alembic upgrade head
uv run alembic check
```

Runtime startup never calls SQLAlchemy `create_all` for a live PostgreSQL
database.

## Validation and release evidence

```bash
make check
make test-deno
make check-security
make build-release
make check-release
git diff --check
```

`make api-sync` regenerates both `openapi.yaml` and
`tools/fleet-tui/src/generated/openapi.ts`; `make api-check` verifies both.

Credentialed promotion additionally requires a passing receipt tied to the
exact candidate SHA:

```bash
FLEET_LIVE=1 uv run python scripts/live_daytona_verify.py \
  --output .scratch/release-ready-mvp/assets/daytona-mvp-proof.json \
  --root-model <approved-root-model> \
  --sub-model <approved-sub-model>
```

The verifier keeps credentials out of Sandboxes and writes only bounded local
evidence. A historical pass does not promote a later SHA. See the
[DSPy RLM and Daytona guide](docs/how-to-guides/dspy-integration.md).
