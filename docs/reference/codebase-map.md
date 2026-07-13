# Backend Codebase Map

The canonical Python backend lives entirely under `src/fleet_rlm/`. The former
compatibility runtime and parallel foundation package no longer exist.

## Modules

| Module | Ownership | May depend on |
| --- | --- | --- |
| `app.py`, `main.py` | FastAPI factory, lifespan, router registration, ASGI entrypoint | composition, API routers |
| `api/` | HTTP translation, identity, dependency aliases, AI SDK UI SSE projection and UIMessage reload | domain interfaces and lifespan-composed modules |
| `chat/` | Turn orchestration, context construction, commit and terminal ordering | RLM, sessions, Skills, files |
| `rlm/` | DSPy signature, fresh-per-Turn RLM construction, budgets, events, runner | DSPy and domain values |
| `daytona/` | Exclusive Daytona SDK boundary, Sandbox/lease/Volume adapters | Daytona SDK, domain values |
| `sessions/` | Session, history, checkpoint, locking, and repository interfaces | domain values only |
| `files/`, `artifacts/` | Attachment staging and Artifact Candidate promotion | storage interfaces, safe paths |
| `skills/` | bundled Skills, authorization, host capability registry, selection, typed task contracts, and progressive tools | domain values and package resources |
| `persistence/` | SQLAlchemy models and repository adapters | sessions/files/artifact interfaces |
| `observability/` | sanitized Turn records and exporters | Runtime Events |
| `cli/` | thin `fleet web` and `fleet-rlm serve-api` launchers | canonical ASGI entrypoint |

## Hard boundaries

- Daytona SDK imports are confined to `fleet_rlm.daytona`.
- FastAPI routes retrieve lifespan-composed modules through dependency aliases;
  routes do not construct repositories, engines, clients, or stores.
- `RLMRunner` owns execution but not Turn Commit or Interpreter Lease release;
  `TurnCoordinator` owns commit, public terminal ordering, and final release.
- Production schema evolution belongs to the root Alembic baseline. Explicit
  `create_tables` calls are limited to hermetic SQLite tests/offline helpers.
- Public chat transport is the AI SDK UI 7 v1 UIMessage protocol over FastAPI
  SSE. Runtime Events remain the internal transport-neutral progress model.
  There is no `/api/v1` compatibility or WebSocket execution surface.

## Public backend paths

- `POST /api/chat`
- `/api/sessions` and `/api/sessions/{id}/turns`
- `/api/files`
- `GET /api/artifacts/{id}`
- `/api/skills`
- `POST /api/runs/{id}/cancel`

There is currently no frontend source tree. A future client will consume the
AI SDK UI 7 SSE contract as a separate implementation effort.
