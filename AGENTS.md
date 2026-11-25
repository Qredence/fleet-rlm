# Fleet-RLM Engineering Guide

Fleet-RLM is a stateless, recursive agentic system built on DSPy 3.5.0 `dspy.RLM`, Daytona isolated sandboxes, Neon Postgres 18, external MLflow 3.18.0, and a Vite + React + shadcn/ui (Base UI AI Elements) frontend.

## Repository Layout

- `backend/` — FastAPI 0.165+ and DSPy 3.5.0 (`uv`, Python `>=3.13,<3.14`) under `backend/src/fleet_rlm/`
  1. `config.py` — Flat `pydantic-settings` (`FLEET_*`) configuration
  2. `db.py` — Neon Postgres 18 SQLAlchemy 2.1 async models for sessions, runs, files, memories, workspace settings, and object cleanup. Uses PgBouncer, `uuidv7()`, and a root-turn partial unique index lock.
  3. `interpreter.py` — Daytona SDK 2.1.0 `.v2` client, filesystem operations, and streamed `DaytonaCodeInterpreter` adapter
  4. `skills.py` — Read-only `SKILL.md` registry and TypeSafe Jev (`Noul` + `Choice`) selector
  5. `agent.py` — DSPy 3.5 `dspy.RLM` runner, recursive `spawn_agent` / `llm_query`, bounded history, and `MemoryGate` consolidation
  6. `ui_stream.py` — Domain-event-to-AI-SDK-v1 (`x-vercel-ai-ui-message-stream: v1`) encoder
  7. `api.py` — Stateless FastAPI 0.165 app with native `EventSourceResponse` SSE over `POST`
  8. `optimize.py` — Offline `dspy.GEPA` & `dspy.experimental.ReAnchor` optimization CLI (`python -m fleet_rlm.optimize`)
  9. `storage.py` — Host-side Neon Object Storage and verification of committed workspace bytes
- `frontend/` — Vite + React + TypeScript + Tailwind v4 + shadcn/ui (Base UI preset) AI Elements SPA consuming `/api/sessions/{id}/chat` natively via `@ai-sdk/react` `useChat`.
- `GLOSSARY.md` — Canonical ubiquitous domain language (`Session`, `Workspace`, `Artifact`, `Turn`, `Run`, `Deployment Settings`, `Compiled Program`, `Calibrated Gate`).
- `docs/PRD.md` & `docs/phases/` — Authoritative specification and phase plans.
- `docker-compose.yml` — External MLflow 3.18.0 local development server (`127.0.0.1:5001`).

## Architecture Boundaries (`import-linter`)

Enforced via `uv run lint-imports` in `backend/pyproject.toml`:
1. The layer order is `(api | optimize)` → `ui_stream` → `(agent | skills | interpreter | db | storage)` → `config`.
2. `db`, `interpreter`, `skills`, and `storage` are mutually independent.
3. Only `api` may import `ui_stream`.
4. `optimize` never imports `api` or `ui_stream`.
5. Only `interpreter` may import `daytona`; only `api` may import `fastapi`.

## Non-Negotiable Guardrails

1. **No In-Process Session State:** Never store sandboxes, locks, or session runtimes in module-level globals or `app.state` keyed by `session_id`.
2. **DSPy 3.5 Discipline:** Never import `dspy.experimental.TypeSafe` (use `dspy.LM("typesafe:jev-latest")` or a pinned snapshot). Never name custom Signature fields `history`, `repl_entry`, `variables_info`, or `iteration`. Keep `Noul`/`Choice`/`Score` outputs in standalone `dspy.Predict` gates, not inside `dspy.RLM`.
3. **FastAPI 0.165 Discipline:** Use `fastapi.sse.EventSourceResponse` + `ServerSentEvent`, never `StreamingResponse`. Never hold `Depends(get_db)` across an SSE stream; use short `async with sessionmaker()` transactions.
4. **Neon PgBouncer Discipline:** Runtime uses pooled `FLEET_DATABASE_URL` with `prepare_threshold=None`; Alembic uses direct `FLEET_DATABASE_DIRECT_URL` with `NullPool`. Never use `pg_advisory_lock`, `LISTEN/NOTIFY`, or SQL `PREPARE`.
5. **Daytona Isolation.** Keep SDK calls inside `interpreter.py`. Keep host LLM, DB, and S3 credentials outside sandbox environments. Unset `DAYTONA_API_URL` and `DAYTONA_V2_API_URL` in shells using v2.
6. **Zero Custom Wire Protocols:** Frontend consumes `x-vercel-ai-ui-message-stream: v1` natively via `@ai-sdk/react` `useChat` + `DefaultChatTransport` (no custom SSE parsers, no BFF proxy).
7. **Offline Optimization Safety:** Always target an isolated Neon branch via `--database-url` and pin the exact TypeSafe Jev snapshot in `FLEET_DECISION_MODEL` when deploying `ReAnchor` artifacts.
8. **Environment-managed infrastructure.** Keep the model gateway, Daytona image and lifecycle, S3 configuration, MLflow experiment, credentials, and worker concurrency in environment variables. `GET /api/settings` exposes non-sensitive status.

## Verification

Run all checks from the repository root:

```bash
make check
```

Or run per-package checks directly:

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run ty check src && uv run pytest
cd frontend && pnpm lint && pnpm typecheck && pnpm test && pnpm build
```
