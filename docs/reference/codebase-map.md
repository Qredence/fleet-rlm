# Backend Codebase Map

The canonical Python backend lives under `src/fleet_rlm/`. The former
compatibility runtime and parallel foundation package no longer exist.

## Modules

| Module | Ownership | May depend on |
| --- | --- | --- |
| `app.py`, `main.py` | FastAPI factory, handlers/routers, static Skill catalog, lifespan selection, ASGI entrypoint | composition, API routers, Skill catalog |
| `composition/` | common inventory plus explicit Daytona, Deno, and private testing wiring | domain modules and adapters |
| `api/` | HTTP translation, local scope, dependency aliases, OpenAPI/SSE projection, UIMessage reload | domain interfaces and composed modules |
| `chat/` | Turn preparation, shared capability preparation, coordinator orchestration, lifecycle finalization, shared Turn Claim policy, Deno environment and sinks | RLM, Sessions, Skills, files |
| `rlm/` | DSPy Signature, model roles, fresh RLM construction, options, events, runner | DSPy and domain values |
| `daytona/` | exclusive SDK boundary: async platform/provisioning, Session ownership, DSPy-only sync interpreter seam, broker, diagnostics, Session Workspace gateways, and the mounted Workspace Memory adapter | Daytona SDK and domain values |
| `sessions/` | Session catalog, Turn input/history, versioned Committed Turn | domain values |
| `files/`, `artifacts/` | Attachment staging, paged/append Session Workspace tools, workspace-wide Memory values/tools, direct Workspace Artifact Candidate staging, Artifact promotion/read | storage interfaces and safe paths |
| `skills/` | bundled catalog, authorization, progressive loading, capability seam, Tool construction | domain values and package resources |
| `persistence/` | SQLAlchemy models and repository adapters | Session/file/Artifact interfaces |
| `observability/` | sanitized failure diagnostics; opt-in Databricks MLflow DSPy tracing | domain errors, Settings |
| `cli/` | supervised Daytona/Deno plus pi-tui, backend launchers, doctor dispatch | ASGI entrypoint and Daytona diagnostics |

## Hard boundaries

- Daytona SDK imports are confined to `fleet_rlm.daytona`.
- `create_app()` may construct only the static Skill catalog/authorizer and empty
  capability registry in addition to the FastAPI shell. Lifespan owns runtime
  repositories, engines, LMs, providers, rollback, and cleanup.
- Routes retrieve runtime modules through dependency aliases. The Skills route
  may recreate only the static in-memory catalog fallback.
- `RLMRunner` owns execution, not Turn Commit or resource release.
  `TurnLifecycle.finish()` owns result/Artifact publication and atomic commit;
  `TurnCoordinator` owns terminal ordering and final cleanup.
- `TurnLifecycleService` maps outcomes to typed Claim commands, and both Turn
  repositories apply them through one `transition_claim()` seam under their
  existing lock/transaction boundaries.
- `LiveKernelResources` owns process-lifetime Daytona resources only;
  `composition/daytona.py` explicitly constructs Turn preparation.
- Daytona Admission bounds acquiring or active Interpreter Leases, not retained
  Session Sandboxes. Released Session Sandboxes are stopped after the explicit
  five-minute idle policy and restarted on the next acquisition.
- Independent Workspace access mounts exactly `workspaces/<workspace_id>` in a
  purpose-labelled ephemeral I/O Sandbox and exposes only its `files/` root.
- During Daytona Turn execution, Workspace Memory uses the fixed `MEMORIES.md`
  at the root of the already workspace-scoped mount. It is distinct from
  Session Workspace under `sessions/{session_id}/workspace/` and Run state under
  `sessions/{session_id}/runs/{run_id}/`.
- In-memory and SQL repositories apply the pure `chat/turn_claim.py` transition
  policy inside their respective lock and transaction boundaries.
- Alembic owns live schema evolution. `create_tables` is limited to explicit
  SQLite test/local helpers.
- Runtime Events are transport-neutral. `api/sse.py` alone owns the public AI
  SDK UI v1 SSE projection.
- `make api-sync` owns both root OpenAPI and generated TUI HTTP types.

The authoritative route inventory and shapes are in
[HTTP API](http-api.md) and `openapi.yaml`.

## Maintained terminal client

| Module | Ownership |
| --- | --- |
| `src/cli-core.ts` | CLI options and verified atomic Artifact download |
| `src/fleet-api-client.ts` | HTTP requests and response handling |
| `src/fleet-turn-stream.ts` | request opening, bounded same-key retry, strict stream lifecycle |
| `src/sse.ts` | SSE framing and closed generated chunk validation |
| `src/tui/runner.ts` | active Run state, submission, cancellation |
| `src/tui/projection.ts` | shared live and durable display projection |
| `src/tui/store.ts` | conversation state and atomic Session hydration |
| `src/tui/application.ts`, `screen.ts`, `transcript.ts` | pi-tui lifecycle, editor/input, cached static native-scrollback layout, and mutable Run activity |
| `src/tui/message-renderer.ts` | complete event, Markdown, result, Artifact, and code presentation |
| `src/tui/commands.ts`, `command-presenter.ts`, `autocomplete.ts` | slash commands, overlays, status, and completion |
| `src/generated/openapi.ts` | generated HTTP types owned by `make api-sync` |

Live and reload use the same display semantics. Evidence is fully expanded and
uses native terminal scrollback; there is no classic renderer, mouse capture,
collapsing state, or application transcript viewport.
