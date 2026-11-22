# Fleet-RLM

Stateless recursive agent workspace powered by **DSPy 3.5.0 (`dspy.RLM`)**, **FastAPI 0.165.0**, **Daytona 0.232.0**, **Neon Postgres 18**, **MLflow 3.18.0**, and a **Vite + React + shadcn/ui (Base UI AI Elements)** SPA.

## Pinned Stack

| Component | Version / Target | Purpose |
| :--- | :--- | :--- |
| **Python** | `>=3.13,<3.14` | Host API, offline optimizer, and Daytona snapshot parity |
| **FastAPI** | `fastapi[standard]>=0.165.0` | Stateless HTTP + native `EventSourceResponse` SSE over `POST` |
| **DSPy** | `dspy==3.5.0` | `dspy.RLM`, `dspy.Flex`, `dspy.History`, `dspy.GEPA`, `ReAnchor`, Decision Types (`Noul`, `Choice`) |
| **Daytona** | `daytona==0.232.0` | Isolated `/workspace` execution contexts and per-session Volume subpaths |
| **Database** | Neon Postgres 18 (`sqlalchemy>=2.1.9`, `alembic>=1.20.1`, `psycopg>=3.3.9`) | `uuidv7()`, recursive `runs`, single-writer partial unique lock, `memories`, `workspace_settings` |
| **Observability** | MLflow `>=3.18.0` (external) | `mlflow.dspy.autolog()` + offline compile/eval tracking |
| **Frontend** | Vite + React + TypeScript + Tailwind v4 + shadcn/ui Base UI AI Elements | `@ai-sdk/react` `useChat` consuming `x-vercel-ai-ui-message-stream: v1` |

## Architecture

- **`backend/src/fleet_rlm/` (8 modules):** `config.py`, `db.py`, `interpreter.py`, `skills.py`, `agent.py`, `ui_stream.py`, `api.py`, and `optimize.py` (enforced by `import-linter` contracts).
- **`frontend/`:** Zero-BFF 3-pane monochrome UI (`SessionsSidebar`, main chat workspace with recursive `SubagentTrace`, streaming `PythonToolTrace`, and `@skill` `SkillPicker`, plus `WorkspaceSidePanel` for session files/memories and `SettingsDialog`).

## Local Setup

1. **Configure backend environment:**
   ```bash
   cp backend/.env.example backend/.env
   ```
   Set `FLEET_DATABASE_URL` (Neon `-pooler`), `FLEET_DATABASE_DIRECT_URL` (Neon direct), `FLEET_DAYTONA_API_KEY`, `FLEET_DAYTONA_VOLUME_ID`, `FLEET_LLM_MODEL`, `FLEET_SUB_LLM_MODEL`, `FLEET_LLM_API_KEY`, and `TYPESAFE_API_KEY`.

   > **Read-only `.env` settings boundary:** Safe workspace overrides (models, RLM/history budgets, Daytona target/timeouts) can be updated via `PATCH /api/settings` / `SettingsDialog` and persist in Neon `workspace_settings`. Infrastructure-bound settings (`llm_base_url`, `daytona_snapshot`, `mlflow_experiment`, plus worker concurrency and credentials) are read-only `.env`-managed values exposed only in `status` and cannot be modified via `/api/settings`.

2. **Install dependencies and migrate Neon:**
   ```bash
   cd backend
   uv sync --python 3.13
   uv run alembic upgrade head
   cd ../frontend
   pnpm install
   ```

3. **Start external MLflow (optional, port `5001` avoids macOS AirPlay on `5000`):**
   ```bash
   make mlflow
   ```

4. **Run development servers:**
   ```bash
   make dev-backend   # http://127.0.0.1:8000
   make dev-frontend  # http://localhost:5173 (proxies /api, /healthz, /readyz)
   ```

## Offline Optimization (`optimize.py`)

Run out-of-band optimization against an ephemeral **Neon branch** URL (`optimize.py` refuses primary `database_url` / `database_direct_url` unless `--allow-direct` is passed):

```bash
cd backend
uv run python -m fleet_rlm.optimize --target rlm --database-url "$NEON_BRANCH_URL"
uv run python -m fleet_rlm.optimize --target skills --database-url "$NEON_BRANCH_URL" --dataset skills.jsonl
uv run python -m fleet_rlm.optimize --target memory --database-url "$NEON_BRANCH_URL" --dataset memory.jsonl
```

When deploying `ReAnchor`-calibrated `skill_selector.json` or `memory_gate.json` artifacts, pin the exact TypeSafe Jev snapshot in `FLEET_DECISION_MODEL` (do not keep floating `typesafe:jev-latest`).

## Verification

```bash
make check
```

Runs backend checks (`ruff check`, `ruff format --check`, `lint-imports`, `ty check src`, and `pytest`) alongside frontend checks (`pnpm lint`, `pnpm typecheck`, Vitest `pnpm test`, and `pnpm build`).
