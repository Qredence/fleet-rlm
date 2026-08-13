# Fleet RLM

Fleet RLM is an RLM-native backend built around FastAPI SSE, `dspy.RLM`, and
Daytona Sandboxes with workspace-scoped durable Volumes. The canonical backend
is `src/fleet_rlm/`; the maintained development client is the pi-tui workspace
under `tools/fleet-tui/`.

## Install and run

```bash
uv sync --all-extras --dev

# supervised backend + pi-tui
uv run fleet cli   # Daytona

# backend only
uv run fleet web
uv run fleet-rlm serve-api --port 8000
```

Select the non-secret runtime policy with `[config] default_profile` in
`config/fleet.toml` or the TUI `/profiles` command, then restart Fleet.
`fleet cli` requires a selected Daytona profile. A mismatch fails before runtime
services start.

`fleet cli` requires Node 22.19+ and pnpm. It waits for backend readiness, writes
backend output under `.fleet_rlm/logs/`, and stops the backend when pi-tui exits.
Forward terminal arguments after `--`, for example:

```bash
uv run fleet cli -- --session <uuid>
```

The terminal uses an alternate-screen viewport and does not own a model, provider key, or
Sandbox. See the [terminal guide](docs/how-to-guides/terminal-tui.md) and
[configuration reference](docs/reference/configuration.md).

### Daytona

Daytona is the full Fleet runtime. The shipped `daytona-recursive` default uses
an OpenCode Go API key and base URL, plus the Daytona key and database URL. The
`daytona-managed` and benchmark profiles use the Databricks AI Gateway instead.
See the [generated profile matrix](docs/reference/profile-matrix.md) for the
policy-derived environment names and token limits. Initialize the configured
database explicitly; startup never applies migrations automatically.

```bash
export FLEET_DATABASE_URL='postgresql+asyncpg://...'
export FLEET_DAYTONA_API_KEY='...'
export FLEET_OPENCODE_GO_API_KEY='...'
export FLEET_OPENCODE_GO_BASE_URL='https://<gateway>/v1'
make daytona-snapshot-check
uv run python scripts/db_init.py
uv run fleet-rlm serve-api --port 8000
```

The committed default is `default_profile = "daytona-recursive"`. Set
`default_profile = "daytona"` for the non-recursive interactive profile, or
select another documented profile before restarting Fleet.

Use `uv run fleet doctor daytona` for an opt-in disposable provider, database,
mount, and interpreter probe before diagnosing a Turn.

## Backend API

- `POST /api/sessions/{session_id}/turns` — local-scope idempotent SSE execution.
- `/api/sessions` — Session CRUD and ordered committed Turn history.
- `/api/attachments` — durable Attachment upload and metadata lookup.
- `/api/artifacts/{artifact_id}` — committed metadata and verified content.
- `GET /api/volume/tree` — Daytona-only bounded read-only logical Workspace
  Volume paths.
- `/api/skills` — bounded system Skill Card discovery for the five bundled
  Skills.
- `PUT /api/runs/{run_id}/cancellation` — durable Run cancellation.

There is no `/api/v1`, WebSocket execution, optimization/evaluation API,
runtime-admin API, caller-selected BYOK profile API, or public Artifact creation.
The Volume tree is a read-only, process-local view rather than a general-purpose
Sandbox filesystem browser.
See the [HTTP API](docs/reference/http-api.md) and generated
[OpenAPI](openapi.yaml).

## Architecture

One Turn validates its deterministic local scope, Attachments, and exact Skill
selections before opening SSE. `TurnCoordinator` begins and prepares execution,
`RLMRunner` runs one fresh native `dspy.RLM`, and `RunLifecycle.finish()` owns
result snapshot handling, Artifact publication, and atomic Turn Commit. The
coordinator then projects the terminal suffix and cleans up Run resources.

The Root uses Python, native Sub-LM queries, or isolated child RLMs according to
the cheapest-sufficient delegation ladder. Recursive children remain one native
level deep; Root-only `rlm_query_batched` provides ordered, bounded sibling
fan-out, and Root verifies and synthesizes their evidence before `SUBMIT`.

Daytona Turns acquire a fresh Interpreter Lease and use Workspace Volume Scope.
Each Turn receives a bounded newest-record digest of Workspace Memory in its
`session_context`; the full `memory/MEMORIES.md` log remains behind the
host-mediated Memory Tools. The RLM may append a record only when the user
explicitly asks to remember something. Memory is immediate workspace state,
not Session History or a Turn-commit record, and survives failed Runs and
Sandbox replacement. Full Session history stays host-side behind the bounded
`read_session_history` Tool.

Read the [architecture](docs/architecture.md), [backend context](src/fleet_rlm/CONTEXT.md),
and [codebase map](docs/reference/codebase-map.md).

## Database

Alembic owns live schema evolution through one canonical baseline. For an
explicit disposable/live database:

```bash
uv run alembic upgrade head
uv run alembic check
```

Runtime startup never calls SQLAlchemy `create_all` for a live PostgreSQL
database.

## Validation and release evidence

```bash
make check
make check-security
make build-release
make check-release
git diff --check
```

`make api-sync` regenerates `openapi.yaml`,
`tools/fleet-tui/src/generated/openapi.ts`, and
`tools/fleet-tui/src/generated/fleet-ui-chunk-validation.ts`;
`make api-check` verifies all three.

Credentialed promotion additionally requires a passing receipt tied to the
exact candidate SHA:

```bash
FLEET_LIVE=1 uv run python scripts/live_daytona_verify.py \
  --output .scratch/release-ready-mvp/assets/daytona-mvp-proof.json
```

The verifier keeps credentials out of Sandboxes and writes only bounded local
evidence. A historical pass does not promote a later SHA. See the
[DSPy RLM and Daytona guide](docs/how-to-guides/dspy-integration.md).
