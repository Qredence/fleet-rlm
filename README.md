# Fleet RLM

**Recursive language-model backend with live streaming, durable sessions, and sandboxed execution.**

Fleet RLM runs [DSPy](https://github.com/stanfordnlp/dspy) `dspy.RLM` behind a compact FastAPI + SSE API. Each turn executes in an isolated [Daytona](https://www.daytona.io/) sandbox with workspace-scoped volumes, host-mediated tools, and a terminal client that streams reasoning, code, and output as it happens.

[![CircleCI](https://dl.circleci.com/status-badge/img/gh/Qredence/fleet-rlm/tree/main.svg?style=svg)](https://dl.circleci.com/status-badge/redirect/gh/Qredence/fleet-rlm/tree/main)
[![PyPI](https://img.shields.io/pypi/v/fleet-rlm?style=flat-square&logo=pypi&logoColor=white)](https://pypi.org/project/fleet-rlm/)
[![Python](https://img.shields.io/badge/python-3.11%20|%203.12%20|%203.13-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-2EA44F?style=flat-square)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-Read%20the%20Docs-2C9ED0?style=flat-square&logo=readthedocs&logoColor=white)](https://fleet-rlm.readthedocs.io/)
[![DSPy](https://img.shields.io/badge/DSPy-3.3+-8B5CF6?style=flat-square)](https://dspy.ai/)
[![FastAPI](https://img.shields.io/badge/FastAPI-SSE-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)

---

## Why Fleet RLM

- **RLM-native** — One fresh `dspy.RLM` per turn with Python REPL execution, native sub-LM queries, and optional recursive child RLMs.
- **Operator-visible streaming** — Reasoning, tool calls, interpreter code, and stdout flow over SSE to the maintained [pi-tui terminal](tools/fleet-tui/).
- **Durable by default** — Sessions, turns, attachments, artifacts, and workspace memory survive across runs.
- **Sandboxed execution** — Daytona interpreters run in isolated sandboxes with bounded workspace volumes and host-mediated memory tools.
- **Policy-driven runtime** — Non-secret behavior lives in `config/fleet.toml`; secrets stay in `FLEET_*` environment variables.

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

Pick a runtime profile in `config/fleet.toml` (`default_profile`; shipped default is `daytona-recursive`), then export the provider and Daytona variables for that profile. See the [profile matrix](docs/reference/profile-matrix.md) for the exact `FLEET_*` names.

```bash
export FLEET_DATABASE_URL='postgresql+asyncpg://...'
export FLEET_DAYTONA_API_KEY='...'
export FLEET_OPENCODE_GO_API_KEY='...'
export FLEET_OPENCODE_GO_BASE_URL='https://<gateway>/v1'

uv run python scripts/db_init.py
```

Startup never applies migrations automatically — initialize the database explicitly before serving.

### 3. Run

**Supervised backend + terminal** (recommended for local development):

```bash
uv run fleet cli
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
| `uv run fleet cli` | Start backend + pi-tui terminal (Daytona profile required) |
| `uv run fleet web` | Start backend only on port 8000 |
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

- [Architecture](docs/architecture.md)
- [Configuration](docs/reference/configuration.md)
- [Terminal UI guide](docs/how-to-guides/terminal-tui.md)
- [DSPy + Daytona integration](docs/how-to-guides/dspy-integration.md)
- [Testing strategy](docs/how-to-guides/testing-strategy.md)

## License

MIT — see [LICENSE](LICENSE).
