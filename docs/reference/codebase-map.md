# Backend Codebase Map

The canonical Python backend lives entirely under `src/fleet_rlm/`. The former
compatibility runtime and parallel foundation package no longer exist.

## Modules

| Module | Ownership | May depend on |
| --- | --- | --- |
| `app.py`, `main.py` | FastAPI/router shell, lifespan selection and cleanup, ASGI entrypoint | composition, API routers |
| `api/` | HTTP translation, deterministic local scope, dependency aliases, AI SDK UI SSE projection and UIMessage reload | domain interfaces and lifespan-composed modules |
| `chat/` | Turn orchestration, context construction, commit and terminal ordering; Deno Run Environment, sinks, reduced capabilities, and RLM factory | RLM, sessions, Skills, files |
| `rlm/` | DSPy signature, fresh-per-Turn RLM construction, RLM Options, events, runner | DSPy and domain values |
| `daytona/` | Exclusive Daytona SDK boundary, Sandbox/lease/Volume adapters | Daytona SDK, domain values |
| `sessions/` | Session Catalog, canonical Turn input/history, and versioned Committed Turn aggregate | domain values only |
| `files/`, `artifacts/` | Attachment staging, private Session Workspace policy/tools, and Artifact Candidate promotion | storage interfaces, safe paths |
| `skills/` | Agent Skills-compatible bundled catalog, authorization, explicit host capability composition, and progressive body/resource tools | domain values and package resources |
| `persistence/` | SQLAlchemy models and repository adapters | sessions/files/artifact interfaces |
| `observability/` | sanitized Turn records and exporters | Runtime Events |
| `cli/` | Daytona/Deno backend-plus-pi-tui supervision, backend-only launchers, and doctor dispatch | canonical ASGI entrypoint, Daytona diagnostics |

## Hard boundaries

- Daytona SDK imports are confined to `fleet_rlm.daytona`.
- `create_app()` constructs no runtime inventory. FastAPI lifespan installs one
  complete Deno or Daytona composition and owns rollback and
  cleanup; routes retrieve it through dependency aliases and do not construct
  repositories, engines, clients, or stores.
- `RLMRunner` owns execution but not Turn Commit or Interpreter Lease release;
  `TurnCoordinator` owns commit, public terminal ordering, and final release.
- Production schema evolution belongs to the root Alembic baseline. Explicit
  `create_tables` calls are limited to private tests and Deno SQLite helpers.
- Public chat transport is the AI SDK UI 7 v1 UIMessage protocol over FastAPI
  SSE. Runtime Events remain the internal transport-neutral progress model.
  There is no `/api/v1` compatibility or WebSocket execution surface.

## Public backend paths

- `POST /api/sessions/{id}/turns`
- `/api/sessions` and `/api/sessions/{id}/turns`
- `/api/attachments`
- `GET /api/artifacts/{id}`
- `/api/skills`
- `PUT /api/runs/{id}/cancellation`

## Maintained terminal client

| Module | Ownership |
| --- | --- |
| `tools/fleet-tui/src/fleet-turn-stream.ts` | request opening, bounded same-key retry, strict UI SSE lifecycle |
| `tools/fleet-tui/src/sse.ts` | SSE framing and closed generated `FleetUIMessageChunk` validation |
| `tools/fleet-tui/src/tui/projection.ts` | shared live-chunk and durable-Turn display projection |
| `tools/fleet-tui/src/tui/store.ts` | conversation state and atomic Session hydration |
| `tools/fleet-tui/src/tui/theme.ts` | white-and-gray palette and shared visual hierarchy |
| `tools/fleet-tui/src/tui/application.ts` | pi-tui lifecycle, editor, input routing, progress, and cleanup |
| `tools/fleet-tui/src/tui/screen.ts` | flat native-scrollback screen and progressive compact layout |
| `tools/fleet-tui/src/tui/message-renderer.ts` | complete static event, Markdown, Result, and code presentation |

The client has no classic renderer. Live and reload use the same display
semantics, including visible typed structured Result cards. Execution evidence
is fully expanded and uses native terminal scrollback rather than application
focus, collapsing, or viewport state. A future graphical client is a separate
implementation effort.

`fleet cli` and `fleet deno` supervise this client against a selected local
backend while keeping backend output out of pi-tui. `daytona/diagnostics.py` owns
the opt-in disposable Sandbox doctor and never composes Fleet domain stores.
