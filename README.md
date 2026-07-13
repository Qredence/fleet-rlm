# Fleet RLM

Fleet RLM is an RLM-native backend built around FastAPI SSE, `dspy.RLM`, and
Daytona Sandboxes with workspace-scoped durable Volumes.

The canonical backend is `src/fleet_rlm/`. The former compatibility backend has
been removed. The existing React frontend has not yet been adapted to the new
SSE contract and is intentionally outside the backend cutover.

## Install and run

```bash
uv sync --all-extras --dev
uv run fleet web
# equivalent backend launcher
uv run fleet-rlm serve-api
```

The default app is offline/hermetic. Live DSPy and Daytona composition is
explicit:

```bash
export FLEET_LIVE_KERNEL=true
export FLEET_DATABASE_URL='postgresql+asyncpg://...'
export FLEET_DAYTONA_API_KEY='...'
export FLEET_LLM_API_KEY='...'
export FLEET_AUTH_MODE=neon
export FLEET_NEON_AUTH_URL='https://...'
uv run python scripts/db_init.py
uv run fleet-rlm serve-api
```

## Backend API

- `POST /api/chat` — authenticated SSE Turn execution.
- `/api/sessions` — Session CRUD and ordered Turn history.
- `/api/files` — durable Attachment upload and metadata lookup.
- `GET /api/artifacts/{artifact_id}` — committed Artifact retrieval.
- `/api/skills` — authorized Skill Card discovery.
- `POST /api/runs/{run_id}/cancel` — authorized Run cancellation.

There is no `/api/v1`, WebSocket execution, optimization/evaluation API,
runtime-admin API, Volume browser, BYOK profile API, or public Artifact creation.
See [HTTP API](docs/reference/http-api.md) and generated [OpenAPI](openapi.yaml).

## Architecture

One Turn validates identity and Attachment references before opening SSE,
restores durable Session History, acquires a Daytona Interpreter Lease, creates
a fresh `dspy.RLM`, executes host-mediated tools, promotes private Artifact
Candidates, commits the Turn transactionally, emits committed Artifact events,
then emits exactly one terminal Runtime Event and releases the lease.

Read [architecture](docs/architecture.md), the [backend context](src/fleet_rlm/CONTEXT.md),
and the [codebase map](docs/reference/codebase-map.md).

## Database

Production schema evolution is owned by Alembic and starts from one fresh
canonical baseline:

```bash
export FLEET_DATABASE_URL='postgresql+asyncpg://...'
uv run alembic upgrade head
uv run alembic check
```

Runtime live startup never calls SQLAlchemy `create_all`.

## Validation

```bash
make check
FLEET_LIVE=1 uv run pytest tests/live/backend/test_exit_bar_l1_promotion.py -q
FLEET_LIVE=1 uv run pytest tests/live/backend/test_exit_bar_l2_adversarial.py -q
```

`make api-sync` regenerates only root `openapi.yaml`; frontend generated
contracts remain deferred to the frontend adaptation effort.
