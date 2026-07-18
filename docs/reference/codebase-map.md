# Backend Codebase Map

The canonical Python backend lives under `src/fleet_rlm/`. The former
compatibility runtime and parallel foundation package no longer exist.

## Modules

| Module | Ownership | May depend on |
| --- | --- | --- |
| `app.py`, `main.py` | FastAPI factory, handlers/routers, static Skill catalog, lifespan selection, ASGI entrypoint | composition, API routers, Skill catalog |
| `composition/` | common inventory plus explicit Daytona, Deno, and private testing wiring | domain modules and adapters |
| `api/` | HTTP translation, local scope, dependency aliases, OpenAPI/SSE projection, UIMessage reload | domain interfaces and composed modules |
| `chat/` | Turn preparation, coordinator orchestration, lifecycle finalization, Deno environment and sinks | RLM, Sessions, Skills, files |
| `rlm/` | DSPy Signature, model roles, fresh RLM construction, options, events, runner | DSPy and domain values |
| `daytona/` | exclusive SDK boundary, Sandbox/lease/Volume/interpreter adapters | Daytona SDK and domain values |
| `sessions/` | Session catalog, Turn input/history, versioned Committed Turn | domain values |
| `files/`, `artifacts/` | Attachment staging, Session Workspace tools, Artifact Candidate promotion/read | storage interfaces and safe paths |
| `skills/` | bundled catalog, authorization, progressive loading, capability seam, Tool construction | domain values and package resources |
| `persistence/` | SQLAlchemy models and repository adapters | Session/file/Artifact interfaces |
| `observability/` | sanitized Turn records and exporters | Runtime Events |
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
| `src/tui/application.ts`, `screen.ts` | pi-tui lifecycle, editor/input, static native-scrollback layout |
| `src/tui/message-renderer.ts` | complete event, Markdown, result, Artifact, and code presentation |
| `src/tui/commands.ts`, `command-presenter.ts`, `autocomplete.ts` | slash commands, overlays, status, and completion |
| `src/generated/openapi.ts` | generated HTTP types owned by `make api-sync` |

Live and reload use the same display semantics. Evidence is fully expanded and
uses native terminal scrollback; there is no classic renderer, mouse capture,
collapsing state, or application transcript viewport.
