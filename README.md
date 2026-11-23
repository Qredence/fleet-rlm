# Fleet-RLM

**Fleet-RLM** is a stateless, recursive AI agent workspace powered by **DSPy 3.5 (`dspy.RLM`)**, isolated **Daytona** Python sandboxes, **Neon Postgres 18**, and a **React** SPA. Rather than stuffing large files into an LLM prompt window, Fleet-RLM stores Input Files in a persistent per-session Workspace, writes and runs Python to analyze and cross-check data, delegates bounded recursive subtasks, streams live execution traces to the browser, and curates long-term Memory across sessions.

---

## Capabilities

- **Programmatic Long Context:** Upload CSV/Parquet datasets, multi-sheet Excel workbooks, PDFs, or code (up to 100 MiB) into `/workspace/files`. The agent inspects and transforms data using `pandas`, `pyarrow`, `openpyxl`, `pypdf`, `scipy`, `matplotlib`, and `pillow`, then saves downloadable Artifacts to `/workspace/artifacts`.
- **Recursive Reasoning & Live Traces:** Complex Tasks decompose dynamically into concurrent `llm_query_batched` map-reduce calls and isolated recursive `spawn_agent` Subagent Runs under configurable Recursion Depth and Call Budget limits, streamed natively via AI SDK v1 SSE into nested UI traces.
- **Progressive-Disclosure Skills (`@skill`):** Includes 7 read-only `SKILL.md` Skills (`data-analysis`, `spreadsheet-audit`, `document-extraction`, `chart-visualization`, `scientific-computing`, `recursive-synthesis`, and `skill-creator`) baked into the Sandbox Snapshot—automatically scored per Turn by TypeSafe Jev (`Noul` + `Choice`) or explicitly pinned with `@skill`.
- **4-Tier Memory with Human Review:** Pairs ephemeral per-Run Execution Contexts and bounded Episode History with Neon full-text searchable Session/Global Memories (`tsvector` GIN) consolidated after each Turn (high-confidence facts auto-approved; lower-confidence candidates queued in the UI for review) and versioned offline Compiled Programs / Calibrated Gates.
- **Stateless Workers, Durable State:** Zero in-process session caches or background daemons. Any FastAPI worker handles any Turn over Neon Postgres 18 (5 tables with a single-writer partial unique lock) and per-session Daytona Volume subpaths.
- **Offline Optimization & Observability:** Out-of-band `dspy.GEPA` program compilation and 5-fold `ReAnchor` gate calibration against isolated Neon branches, paired with external MLflow 3.18 tracing.

---

## Quick Start

**Prerequisites:** Python `3.13` with `uv`, Node `22+` with `pnpm`, a **Neon Postgres 18** database (pooled + direct URLs), a **Daytona** API key + Volume + Snapshot built from [`backend/snapshot/Dockerfile`](./backend/snapshot/Dockerfile), LLM and TypeSafe (`TYPESAFE_API_KEY`) credentials, and Docker *(optional, for local MLflow)*.

1. **Configure environment variables:**
   ```bash
   cp backend/.env.example backend/.env
   ```
   Set `FLEET_DATABASE_URL` (`-pooler`), `FLEET_DATABASE_DIRECT_URL` (direct), `FLEET_DAYTONA_API_KEY`, `FLEET_DAYTONA_VOLUME_ID`, `FLEET_LLM_MODEL`, `FLEET_SUB_LLM_MODEL`, `FLEET_LLM_API_KEY`, and `TYPESAFE_API_KEY`. Unset any legacy `DAYTONA_API_URL` or `DAYTONA_V2_API_URL` variables in your shell before migrating future workloads to `daytona.v2`.

   > **Settings boundary:** Safe Deployment Settings (models, RLM/history budgets, Daytona target/timeouts) can be edited in the UI **Settings** dialog (`PATCH /api/settings`) and persist in Neon `workspace_settings`. Infrastructure settings (`llm_base_url`, `daytona_snapshot`, `mlflow_experiment`, credentials, concurrency) remain read-only `.env` values exposed only as status.

2. **Install dependencies and run database migrations:**
   ```bash
   cd backend && uv sync --python 3.13 && uv run alembic upgrade head
   cd ../frontend && pnpm install
   ```

3. **Start external MLflow *(optional — port `5001` avoids macOS AirPlay on `5000`)*:**
   ```bash
   make mlflow
   ```

4. **Run the backend and frontend:**
   ```bash
   make dev-backend   # FastAPI on http://127.0.0.1:8000
   make dev-frontend  # Vite SPA on http://localhost:5173 (proxies /api, /healthz, /readyz)
   ```

---

## Architecture & Stack

- **`backend/src/fleet_rlm/` (8 modules):** `config.py`, `db.py`, `interpreter.py`, `skills.py`, `agent.py`, `ui_stream.py`, `api.py`, and `optimize.py`, strictly ordered and isolated via `import-linter` contracts.
- **`frontend/`:** Zero-BFF 3-pane monochrome UI (`SessionsSidebar`, main chat workspace with `ChatComposer`, `PythonToolTrace`, and recursive `SubagentTrace`, plus `WorkspaceSidePanel` and `SettingsDialog`) consuming `x-vercel-ai-ui-message-stream: v1` via `@ai-sdk/react` `useChat`.

| Component | Version / Target | Role |
| :--- | :--- | :--- |
| **Python** | `>=3.13,<3.14` | Host API, offline optimizer, and Daytona Snapshot parity |
| **FastAPI** | `fastapi[standard]>=0.165.0` | Stateless REST + native `EventSourceResponse` SSE over `POST` |
| **DSPy** | `dspy==3.5.0` | `dspy.RLM`, `dspy.Flex`, `dspy.History`, `dspy.GEPA`, `ReAnchor`, `Noul`/`Choice` |
| **Daytona** | `daytona[v0-sdk]>=2.1.0,<3` (`.v0`) | Isolated `/workspace` execution contexts and per-session Volume subpaths |
| **Database** | Neon Postgres 18 (`sqlalchemy>=2.1.9`, `alembic>=1.20.1`) | `uuidv7()`, recursive `runs`, single-writer turn lock, `memories`, `workspace_settings` |
| **Observability** | MLflow `>=3.18.0` (external) | `mlflow.dspy.autolog()` + offline compile/eval artifact tracking |
| **Frontend** | Vite + React 19 + TypeScript + Tailwind v4 + shadcn/ui | Native AI SDK v1 streaming chat, file tree, memory review, settings |

---

## Offline Optimization (`optimize.py`)

Run out-of-band optimization against an ephemeral **Neon branch** (`optimize.py` refuses configured primary URLs unless `--allow-direct` is passed):

```bash
cd backend
uv run python -m fleet_rlm.optimize --target rlm --database-url "$NEON_BRANCH_URL"
uv run python -m fleet_rlm.optimize --target skills --database-url "$NEON_BRANCH_URL" --dataset skills.jsonl
uv run python -m fleet_rlm.optimize --target memory --database-url "$NEON_BRANCH_URL" --dataset memory.jsonl
```

When deploying `ReAnchor`-calibrated `skill_selector.json` or `memory_gate.json` gates, pin the exact TypeSafe Jev snapshot in `FLEET_DECISION_MODEL` instead of floating `typesafe:jev-latest`.

---

## Verification & Documentation

```bash
make check
```

Runs backend checks (`ruff check`, `ruff format --check`, `lint-imports`, `ty check src`, `pytest`) and frontend checks (`pnpm lint`, `pnpm typecheck`, Vitest `pnpm test`, `pnpm build`).

- [GLOSSARY.md](./GLOSSARY.md) — Canonical domain vocabulary (`Session`, `Workspace`, `Artifact`, `Turn`, `Run`, `Deployment Settings`, `Compiled Program`, `Calibrated Gate`)
- [docs/PRD.md](./docs/PRD.md) — Architecture specification and non-negotiable engineering guardrails
- [AGENTS.md](./AGENTS.md) — Developer guide, module boundaries, and verification commands
- [frontend/README.md](./frontend/README.md) — Frontend development notes
- [CHANGELOG.md](./CHANGELOG.md) — Release history and migration notes
