# Fleet RLM

**Recursive language-model backend with live streaming, durable sessions, and sandboxed execution.**

Fleet RLM runs [DSPy](https://github.com/stanfordnlp/dspy) `dspy.RLM` behind a compact FastAPI + SSE API. Turns execute in [Daytona](https://www.daytona.io/) sandboxes with workspace-scoped volumes, host-mediated tools, and a terminal client that streams reasoning, code, and output. The selected runtime reuses a healthy Session sandbox across sequential successful Turns; recursive children receive isolated disposable sandboxes.

[![CircleCI](https://dl.circleci.com/status-badge/img/gh/Qredence/fleet-rlm/tree/main.svg?style=svg)](https://dl.circleci.com/status-badge/redirect/gh/Qredence/fleet-rlm/tree/main)
[![PyPI](https://img.shields.io/pypi/v/fleet-rlm?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/fleet-rlm/)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-2EA44F?style=flat-square)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-Read%20the%20Docs-2C9ED0?style=flat-square&logo=readthedocs&logoColor=white)](https://fleet-rlm.readthedocs.io/)
[![DSPy](https://img.shields.io/badge/DSPy-3.3.1-8B5CF6?style=flat-square)](https://dspy.ai/)
[![FastAPI](https://img.shields.io/badge/FastAPI-SSE-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)

---

## Why Fleet RLM

- **RLM-native** — Native `dspy.RLM` execution with fresh invocation history and Turn bindings, native sub-LM queries, and optional recursive child RLMs.
- **Operator-visible streaming** — Reasoning, tool calls, interpreter code, and stdout flow over SSE to the maintained [pi-tui terminal](tools/fleet-tui/).
- **Durable by default** — Sessions, turns, attachments, artifacts, and workspace memory survive across runs.
- **Sandboxed execution** — Daytona interpreters run in isolated sandboxes with bounded workspace volumes and host-mediated memory tools.
- **Policy-driven runtime** — Non-secret behavior lives in `config/fleet.toml`; secret values stay in environment variables.

## Current state

- **Dependency baseline** — `pyproject.toml` pins DSPy 3.3.1, Daytona 0.210.0, and MLflow 3.16.0; the optional `optimize` extra pins GEPA 0.1.4. The exact-version guard (`CERTIFIED_DSPY_VERSION`) rejects DSPy drift. Dependency checks and historical receipts do not certify a new source revision.
- **Runtime migration** — `runtime.variant = "legacy"` is the only selectable architecture. ADR 006's native interpreter and capsule mechanics are experimental; remote process containment and production cutover remain gated. See the [implementation status](docs/decisions/006-implementation-status.md).
- **Turn orchestration** — `TurnCoordinator` is the sole owner of the claim → cleanup path with atomic turn commit; the stream vocabulary is the closed v1 Runtime Event set (freeze suites in `tests/freeze/`).
- **Recursive RLM** — Native DSPy 3.3.1 child RLMs run under one contracted runtime owner (`src/fleet_rlm/daytona/recursive_child_runtime.py`) with a child deadline fence and zero-leak certification lanes in `tests/live/backend/`.
- **Tools** — Explicit Session Workspace (7 tools) and Project (6 tools) hosts; cross-sandbox Workspace Memory append coordination is unsupported by design.
- **Optimization** — `src/fleet_rlm/optimization/gepa_runner.py` drives the official `gepa.optimize` API under a `max_metric_calls` budget; no `fleet optimize` CLI exists yet.
- **Live evidence** — `FLEET_LIVE=1` serial lanes write receipts under `.fleet-evidence/receipts/` (archived sets under `.fleet-evidence/receipts-archive/`); see the [testing strategy](docs/how-to-guides/testing-strategy.md).

## Quick start

### 1. Install

```bash
git clone https://github.com/Qredence/fleet-rlm.git
cd fleet-rlm
uv sync --all-extras --dev
pnpm --dir tools/fleet-tui install --frozen-lockfile
```

You need **Node 22.19+** and **pnpm** for the terminal client (`fleet cli`). `uv sync` does not install TUI dependencies; run the `pnpm` step above before `fleet cli`.

### 2. Configure credentials

Pick a runtime profile in `config/fleet.toml` (`default_profile`; shipped default is `daytona-recursive`), then export the provider and Daytona variables for that profile. See the [profile matrix](docs/reference/profile-matrix.md) for the exact environment names.

Fleet connects through an OpenAI-compatible Chat Completions base URL, so
Databricks is only the shipped example. To use OpenAI or another compatible
provider, update the selected profile's `model`, `api_key_env`, and
`base_url_env` entries in `config/fleet.toml`; the base URL is typically the
provider's `/v1` root, such as `https://api.openai.com/v1`.

```bash
export FLEET_DATABASE_URL='postgresql+asyncpg://...'
export FLEET_DAYTONA_API_KEY='...'
export DATABRICKS_TOKEN='...'
export FLEET_LLM_BASE_URL='https://<workspace-host>/ai-gateway/mlflow/v1'

uv run python scripts/db_init.py
```

`FLEET_LLM_BASE_URL` is the committed Fleet chat-inference base; the client
appends `/chat/completions`. Keep `DATABRICKS_HOST` for Databricks MLflow or
evaluation tooling. `FLEET_DATABRICKS_AI_GATEWAY_BASE_URL` is reserved for
explicit custom or benchmark paths and is not read by the shipped profile.

Startup never applies migrations automatically — initialize the database explicitly before serving.

### 3. Run

**Supervised backend + terminal** (recommended for local development):

```bash
uv run fleet cli
```

For an explicitly selected policy, pass its non-secret profile name to both
launchers. The Phase 4 campaign profile is opt-in and does not change ordinary
defaults:

```bash
uv run fleet-rlm serve-api --profile phase4-campaign --port 8000
uv run fleet cli --profile phase4-campaign
```

**Backend only:**

```bash
uv run fleet web
# or
uv run fleet-rlm serve-api --port 8000
```

Resume a durable session:

```bash
uv run fleet cli -- --session <session-uuid>
```

Before your first turn, verify Daytona connectivity:

```bash
uv run fleet doctor daytona
```

> **Profile mismatch fails fast.** `fleet cli` requires a Daytona profile that matches your credentials. Select profiles with `/profiles` in the TUI or edit `default_profile`, then restart Fleet.

FastAPI is Fleet's canonical backend interface. `fleet cli` supervises that
backend and attaches the pi-tui client; the TUI does not define a second
execution path. An explicit `--profile NAME` is validated before provider,
database, Daytona, or TUI initialization. The existing `default_profile`
selection remains unchanged when the option is omitted, and explicit profiles
cannot be combined with `--reload`.

The Phase 4 campaign sends candidate and frozen-baseline arms C/D through the
public FastAPI/SSE transport. Arms A/B remain direct DSPy ablations so they are
deliberate non-recursive comparisons rather than production transport
certification. The earlier receipt at
`.scratch/benchmark-reports/phase4-ablation-decf0da7.json` is immutable,
incomplete, and superseded; it is not value proof.

## How a turn works

```text
Client  →  POST /api/sessions/{id}/turns  →  SSE stream
                │
                ├─ validate scope, attachments, skills
                ├─ TurnCoordinator opens run + prepares context
                ├─ RLMRunner executes one native dspy.RLM in Daytona
                ├─ stream reasoning, tools, code, output events
                └─ RunLifecycle commits result, artifacts, and turn history
```

The root agent can answer directly, delegate to sub-LMs, or fan out bounded recursive child RLMs. Session history stays host-side; workspace memory (`memory/MEMORIES.md`) persists across sandbox replacement.

## Commands

| Command | What it does |
| --- | --- |
| `uv run fleet cli` | Start the canonical FastAPI backend + pi-tui terminal (Daytona profile required) |
| `uv run fleet web` | Start the canonical FastAPI backend only on port 8000 |
| `uv run fleet-rlm serve-api --profile NAME` | Start the backend with an explicit non-secret profile |
| `uv run fleet doctor daytona` | Opt-in disposable probe of provider, DB, mounts, interpreter |
| `uv run python scripts/db_init.py` | Initialize or upgrade database to Alembic head |
| `make check` | Default validation lane (backend + TUI) |

Backend logs for supervised runs: `.fleet_rlm/logs/`.

## API surface

| Endpoint | Purpose |
| --- | --- |
| `POST /api/sessions/{session_id}/turns` | Idempotent turn execution over SSE |
| `/api/sessions` | Session CRUD and committed turn history |
| `/api/attachments` | Durable attachment upload and lookup |
| `/api/artifacts/{artifact_id}` | Committed artifact metadata and content |
| `GET /api/volume/tree` | Bounded read-only workspace volume tree (Daytona) |
| `/api/files` | Independent Workspace `files/` namespace management |
| `POST /api/sessions/{session_id}/traces/feedback` | Record feedback for a Session-owned execution trace |
| `/api/settings` | Loopback-only non-secret runtime policy inspection and editing |
| `/api/skills` | Bundled skill card discovery |
| `PUT /api/runs/{run_id}/cancellation` | Durable run cancellation |

Full contract: [HTTP API reference](docs/reference/http-api.md) and [OpenAPI](openapi.yaml).

## Project layout

| Path | Role |
| --- | --- |
| `src/fleet_rlm/` | Canonical Python backend |
| `tools/fleet-tui/` | Maintained pi-tui terminal client |
| `config/fleet.toml` | Runtime policy (profiles, limits, tracing) |
| `migrations/` | Alembic schema |
| `docs/` | Architecture, guides, and reference |

## Development

```bash
make check                 # lint, typecheck, tests (default lane)
make api-sync              # regenerate OpenAPI + TUI types
make check-security        # security scans
```

Contributing workflow and architecture rules: [CONTRIBUTING.md](CONTRIBUTING.md).

Key docs:

- [Architecture](ARCHITECTURE.md)
- [P41 behavior freeze](docs/reference/behavior-freeze.md)
- [Configuration](docs/reference/configuration.md)
- [Terminal UI guide](docs/how-to-guides/terminal-tui.md)
- [DSPy + Daytona integration](docs/how-to-guides/dspy-integration.md)
- [Testing strategy](docs/how-to-guides/testing-strategy.md)

## License

MIT — see [LICENSE](LICENSE).
