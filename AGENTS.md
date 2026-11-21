# Fleet-RLM Engineering Guide

Fleet-RLM is a stateless, recursive agentic system built on DSPy 3.5.0 `dspy.RLM`, Daytona isolated sandboxes, Neon Postgres 18, external MLflow 3.18.0, and a Vite + React + shadcn/ui (Base UI AI Elements) frontend.

## Repository Layout

- `backend/` — FastAPI 0.165+ + DSPy 3.5.0 backend (`uv`, Python `>=3.13,<3.14`), structured as 8 focused modules under `backend/src/fleet_rlm/`:
  1. `config.py` — Flat `pydantic-settings` (`FLEET_*`) configuration
  2. `db.py` — Neon Postgres 18 SQLAlchemy 2.1 async models, PgBouncer pool, `uuidv7()`, root-turn partial unique index lock, and `memories`
  3. `interpreter.py` — All Daytona 0.232.0 SDK interactions and `DaytonaCodeInterpreter` adapter
  4. `skills.py` — Read-only `SKILL.md` registry and TypeSafe Jev (`Noul` + `Choice`) selector
  5. `agent.py` — DSPy 3.5 `dspy.RLM` runner, recursive `spawn_agent` / `llm_query`, bounded history, and `MemoryGate` consolidation
  6. `ui_stream.py` — Domain-event-to-AI-SDK-v1 (`x-vercel-ai-ui-message-stream: v1`) encoder
  7. `api.py` — Stateless FastAPI 0.165 app with native `EventSourceResponse` SSE over `POST`
  8. `optimize.py` — Offline `dspy.GEPA` & `dspy.experimental.ReAnchor` optimization CLI (`python -m fleet_rlm.optimize`)
- `frontend/` — Vite + React + TypeScript + Tailwind v4 + shadcn/ui (Base UI preset) AI Elements SPA consuming `/api/sessions/{id}/chat` natively via `@ai-sdk/react` `useChat`.
- `docs/PRD.md` & `docs/phases/` — Authoritative specification and phase plans.
- `docker-compose.yml` — External MLflow 3.18.0 local development server (`127.0.0.1:5001`).

## Architecture Boundaries (`import-linter`)

Enforced via `uv run lint-imports` in `backend/pyproject.toml` (4 contracts):
1. Layer order: `(api | optimize)` → `ui_stream` → `(agent | skills | interpreter | db)` → `config`.
2. `db`, `interpreter`, and `skills` are mutually independent.
3. Only `api` may import `ui_stream`.
4. `optimize` never imports `api` or `ui_stream`.

## Non-Negotiable Guardrails

1. **No In-Process Session State:** Never store sandboxes, locks, or session runtimes in module-level globals or `app.state` keyed by `session_id`.
2. **DSPy 3.5 Discipline:** Never import `dspy.experimental.TypeSafe` (use `dspy.LM("typesafe:jev-latest")` or a pinned snapshot). Never name custom Signature fields `history`, `repl_entry`, `variables_info`, or `iteration`. Keep `Noul`/`Choice`/`Score` outputs in standalone `dspy.Predict` gates, not inside `dspy.RLM`.
3. **FastAPI 0.165 Discipline:** Use `fastapi.sse.EventSourceResponse` + `ServerSentEvent`, never `StreamingResponse`. Never hold `Depends(get_db)` across an SSE stream; use short `async with sessionmaker()` transactions.
4. **Neon PgBouncer Discipline:** Runtime uses pooled `FLEET_DATABASE_URL` with `prepare_threshold=None`; Alembic uses direct `FLEET_DATABASE_DIRECT_URL` with `NullPool`. Never use `pg_advisory_lock`, `LISTEN/NOTIFY`, or SQL `PREPARE`.
5. **Daytona Isolation:** Keep 100% of `daytona` imports inside `interpreter.py`. Never inject host LLM/DB keys into sandbox env vars.
6. **Zero Custom Wire Protocols:** Frontend consumes `x-vercel-ai-ui-message-stream: v1` natively via `@ai-sdk/react` `useChat` + `DefaultChatTransport` (no custom SSE parsers, no BFF proxy).
7. **Offline Optimization Safety:** Always target an isolated Neon branch via `--database-url` and pin the exact TypeSafe Jev snapshot in `FLEET_DECISION_MODEL` when deploying `ReAnchor` artifacts.

## Verification

Run all checks from the repository root:

```bash
make check
```

Or run per-package checks directly:

```bash
cd backend && uv run ruff check . && uv run lint-imports && uv run pytest
cd frontend && pnpm lint && pnpm typecheck && pnpm build
```
