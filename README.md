# fleet-rlm

[![PyPI version](https://img.shields.io/pypi/v/fleet-rlm.svg)](https://pypi.org/project/fleet-rlm/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/Qredence/fleet-rlm/actions/workflows/ci.yml/badge.svg)](https://github.com/Qredence/fleet-rlm/actions/workflows/ci.yml)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/fleet-rlm?period=monthly&units=INTERNATIONAL_SYSTEM&left_color=MAGENTA&right_color=BLACK&left_text=downloads%2Fmonth)](https://pepy.tech/projects/fleet-rlm)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/Qredence/fleet-rlm)

![fleet-rlm thumbnail](src/frontend/public/branding/thumbnail.png)

`fleet-rlm` is a persistent Daytona-backed recursive DSPy workbench. Give it a task and optional context - files, URLs, pasted text, repository refs, datasets, or prior session state - and it adapts between direct reasoning, tool use, sandboxed execution, recursive sub-task delegation, and offline prompt optimization.

It is built for developers and AI engineers who want an inspectable Web UI and CLI around long-context task execution without hand-rolling WebSocket transport, session persistence, Daytona lifecycle management, execution traces, and recursive DSPy orchestration.

[Documentation](https://fleet-rlm.readthedocs.io/) | [Source docs](docs/) | [Discord](https://discord.gg/ebgy7gtZHK) | [Contributing](CONTRIBUTING.md) | [Changelog](CHANGELOG.md) | [Paper](https://arxiv.org/abs/2512.24601)

## Quick Start

Install the published package in a `uv` project:

```bash
uv init
uv add fleet-rlm
uv run fleet web
```

Open `http://127.0.0.1:8000`.

If you already have a `uv` project, skip `uv init` and run `uv add fleet-rlm`.
Published installs include built frontend assets, so normal users do not need a separate `pnpm` or frontend build step.

## What You Can Do

- Run adaptive task sessions in `Workspace`.
- Attach context from local files, staged documents, pasted text, URLs, repository URLs, and existing session history.
- Watch reasoning, tool calls, sandbox activity, warnings, final answers, summaries, and generated evidence.
- Optimize registered DSPy modules or Fleet skills with dataset upload, run history, result inspection, and comparisons.
- Browse persisted runtime files and artifacts in `Volumes`.
- Inspect runtime health, model connectivity, Daytona connectivity, and local runtime settings in `Settings`.
- Manage LLM profiles, role bindings, auth state, sessions, sandboxes, traces, and runtime diagnostics over the FastAPI API.
- Use terminal chat, HTTP, WebSocket, Daytona smoke checks, snapshot bootstrap, and offline DSPy optimization from the CLI.

The maintained routed product surfaces are `/app/workspace`, `/app/optimization`, `/app/volumes`, and `/app/settings`. The root route and `/app` redirect to `/app/workspace`.

## Primary Commands

Start the Web UI:

```bash
uv run fleet web
```

Start terminal chat:

```bash
uv run fleet
uv run fleet-rlm chat --trace-mode compact
```

Run the API server directly:

```bash
uv run fleet-rlm serve-api --host 127.0.0.1 --port 8000
```

Validate Daytona connectivity without invoking an LM:

```bash
uv run fleet-rlm daytona-smoke --repo https://github.com/qredence/fleet-rlm.git --ref main
```

Create or refresh the reusable Daytona base snapshot:

```bash
uv run fleet-rlm daytona-snapshot
```

Run offline DSPy optimization for a registered module:

```bash
uv run fleet-rlm optimize list
uv run fleet-rlm optimize <module> <dataset.jsonl> --report
```

See the [CLI reference](docs/reference/cli.md) for the full command surface.

## How It Works

`fleet-rlm` has four main layers:

- **Product and transport shell**: the React Web UI talks to FastAPI HTTP and WebSocket routes.
- **Runtime core**: a DSPy ReAct agent coordinates task execution, tool use, streaming events, and recursive delegation.
- **Daytona substrate**: sandbox interpreters, durable mounted roots, child-sandbox isolation, and execution artifacts keep work inspectable and persistent.
- **Persistence and optimization services**: SQLModel-backed sessions, Neon/Postgres or SQLite storage, LLM profile management, trace feedback, and GEPA/DSPy optimization keep runs recoverable and improvable.

The runtime is goal-first rather than repo-first. A repository is one possible context source, alongside documents, URLs, pasted text, durable files, and previous session state.

For deeper detail:

- [Product spec](docs/explanation/product-spec.md)
- [Architecture overview](docs/architecture.md)
- [Recursive RLM isolation](docs/architecture.md#recursive-rlm-isolation)
- [Backend codebase map](docs/reference/codebase-map.md)
- [Frontend/backend integration](docs/reference/frontend-backend-integration.md)
- [HTTP and WebSocket API](docs/reference/http-api.md)

## Codetree

The repository is organized into well-defined packages with explicit ownership and import boundaries. The backend (`src/fleet_rlm/`) contains eight canonical packages: `api/` (FastAPI transport), `runtime/` (DSPy agent core), `integrations/` (Daytona, database, observability), `config/` (constants), `quality/` (offline evaluation and optimization), `cli/` (operator commands), `migrations/` (Alembic schema migrations), and `ui/` (packaged frontend assets). The frontend (`src/frontend/src/`) contains six canonical packages: `features/` (feature modules), `components/agent-elements/` (design system), `lib/workspace/` (WS adapter and runtime), `routes/` (TanStack Router), `lib/rlm-api/` (backend API client), and `config/` (environment configuration).

Import boundaries are enforced by `make check-codebase-tree`. Key rules:
- `runtime/` may not import from `api.routers`
- `quality/` may import from `runtime/` and `integrations/` but not from `api/`
- Frontend `features/` and `components/` must not import directly from `src/fleet_rlm/` (must use `lib/rlm-api/` for backend types)

See the [codebase map](docs/reference/codebase-map.md) for the full package inventory, ownership, public exports, allowed importers, and off-limits imports.

## Runtime Contract

The current backend runtime is Daytona-backed and exposes these stable public surfaces:

- `/health`
- `/ready`
- `/api/v1/info`
- `/api/v1/auth/*`
- `/api/v1/sessions/*`
- `/api/v1/runtime/*`
- `/api/v1/llm-profiles/*`
- `/api/v1/sandboxes/*`
- `/api/v1/runs/*`
- `/api/v1/optimization/*`
- `/api/v1/traces/feedback`
- `/api/v1/ws/execution`
- `/api/v1/ws/execution/events`

The most commonly integrated endpoints are:

- `/ready`
- `GET /api/v1/auth/me`
- `GET /api/v1/sessions/state`
- `GET/PATCH /api/v1/runtime/settings`
- `GET /api/v1/runtime/status`
- `GET /api/v1/runtime/volume/tree`
- `GET /api/v1/runtime/volume/file`
- `GET /api/v1/optimization/status`
- `POST /api/v1/optimization/run`
- `GET /api/v1/optimization/modules`
- `/api/v1/ws/execution`
- `/api/v1/ws/execution/events`

WebSocket execution frames may include `repo_url`, `repo_ref`, `context_paths`, `batch_concurrency`, and an `execution_mode` hint. Durable mounted roots are `memory/`, `artifacts/`, `buffers/`, and `meta/`. Auth identity comes from the configured auth provider; browser WebSocket auth uses short-lived tickets instead of raw JWT query strings.

The canonical OpenAPI schema is [`openapi.yaml`](openapi.yaml). When backend request or response shapes change, regenerate and verify API artifacts with `make api-sync` and `make api-check`.

## RLM Capability Evaluation

Fleet-RLM has been benchmarked against the published RLM paper and Prime Intellect's official `primeintellect/oolong-rlm` environment.

| Benchmark | Paper RLM(GPT-5) | Fleet-RLM + Gemini 3.1 Pro |
| --- | --- | --- |
| S-NIAH (50 tasks, 50K-200K chars) | solved | **100.0%** |
| OOLONG-Official (`trec_coarse` @ 128K) | **56.5%** | **91.67%** (+35.2 pp) |
| OOLONG synthetic (30 tasks) | 56.5% reference | 74.0% |

The checked-in methodology, caveats, and result breakdown live in [RLM capability evaluation](docs/explanation/rlm-capability-evaluation.md). Local generated result bundles may also exist under `output/`, but the docs page is the stable repository reference.

## Source Development

Clone the repository and install Python dependencies:

```bash
git clone https://github.com/qredence/fleet-rlm.git
cd fleet-rlm
uv sync --all-extras
```

Run the product from source:

```bash
make dev
```

For frontend development:

```bash
cd src/frontend
pnpm install --frozen-lockfile
pnpm run dev
```

The frontend dev server runs on `http://localhost:5173` and proxies API requests to the backend on `http://localhost:8000`.

`make dev` and `uv run fleet web` serve a **built** bundle from `src/frontend/dist` on `http://127.0.0.1:8000`.
In a source checkout, the server never falls back to packaged `fleet_rlm/ui/dist` assets.
Build first with `cd src/frontend && pnpm run build`, or use `pnpm run dev` when you want HMR.

Common source checks:

```bash
make format-check
make lint
make typecheck
make test
make check-docs
make quality-gate
```

Frontend-only checks:

```bash
cd src/frontend
pnpm run api:check
pnpm run type-check
pnpm run lint:robustness
pnpm run lint:style-tokens
pnpm run lint:dead-code
pnpm run test:unit
pnpm run build
```

Or run the full frontend lane:

```bash
cd src/frontend
pnpm run check
```

See [developer setup](docs/how-to-guides/developer-setup.md), [testing strategy](docs/how-to-guides/testing-strategy.md), and [scripts inventory](scripts/README.md) for the maintained contributor workflow.

## Documentation and Validation

Start with the [documentation home](docs/index.md). The most useful entry points are:

- [Installation](docs/how-to-guides/installation.md)
- [Runtime settings](docs/how-to-guides/runtime-settings.md)
- [Troubleshooting](docs/how-to-guides/troubleshooting.md)
- [Agent harness](docs/agent-harness/README.md)
- [CLI reference](docs/reference/cli.md)
- [Python API](docs/reference/python-api.md)
- [Auth reference](docs/reference/auth.md)
- [Database reference](docs/reference/database.md)
- [Daytona architecture](docs/reference/daytona-architecture.md)

For README-only or docs-only edits, run:

```bash
uv run python scripts/check_docs_quality.py --skip-contract-checks
```

When durable docs, command surfaces, generated contracts, or harness links move, run:

```bash
make check-docs
```

## Environment Notes

At minimum, configure an LLM provider before running real task sessions:

```ini
DSPY_LM_MODEL=openai/gpt-4o
DSPY_LLM_API_KEY=sk-...
```

For Daytona-backed sandbox execution, configure:

```bash
export DAYTONA_API_KEY="..."
export DAYTONA_API_URL="https://app.daytona.io/api"
```

See `.env.example` and [installation](docs/how-to-guides/installation.md) for the full environment reference. Do not commit `.env` files or shared secrets.

## Repository Layout

```text
src/fleet_rlm/api/                  FastAPI app, auth, routers, WebSocket transport
src/fleet_rlm/runtime/              DSPy agent runtime, execution helpers, tools
src/fleet_rlm/integrations/daytona/ Daytona interpreter, sandbox lifecycle, durable volumes
src/fleet_rlm/integrations/database/ Postgres repository, schema, migrations-facing models
src/fleet_rlm/integrations/local_store.py SQLite fallback store and local session helpers
src/fleet_rlm/quality/              Offline DSPy evaluation and optimization
src/fleet_rlm/cli/                  `fleet` and `fleet-rlm` entrypoints
src/frontend/                       React/TanStack Web UI, routed product surfaces, generated API client
docs/                               User, contributor, architecture, and reference docs
scripts/                            Maintained helper scripts and validation tooling
```

Generated or synced artifacts should not be edited by hand, including `openapi.yaml`, frontend OpenAPI client files, route trees, and packaged UI assets. Use the commands documented in [AGENTS.md](AGENTS.md) and the [agent harness](docs/agent-harness/README.md).

## License

`fleet-rlm` is released under the [MIT License](LICENSE).
