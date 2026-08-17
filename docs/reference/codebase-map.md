# Backend Codebase Map

The canonical Python backend lives under `src/fleet_rlm/`. The former
compatibility runtime and parallel foundation package no longer exist.

## Modules

| Module | Ownership | May depend on |
| --- | --- | --- |
| `app.py`, `main.py` | FastAPI factory, handlers/routers, static Skill catalog, lifespan selection, ASGI entrypoint | composition, API routers, Skill catalog |
| `composition/` | common inventory plus explicit Daytona and private testing wiring | domain modules and adapters |
| `config.py`, `config_policy.py` | TOML-profile runtime Settings and loopback-only non-secret policy editor | policy document, Settings |
| `json_types.py` | closed `JsonScalar`/`JsonValue` contract | none |
| `snapshot_contract.py` | immutable Daytona Snapshot name policy | none |
| `result_snapshot.py` | private commit-gated typed-result encoding | RLM prediction/usage |
| `api/` | HTTP translation, local scope, dependency aliases, OpenAPI/SSE projection, UIMessage reload | domain interfaces and composed modules |
| `chat/` | Turn preparation, shared capability preparation, coordinator facade, private Turn execution driver, lifecycle finalization, owned post-commit Memory promotion, shared Turn Claim policy | RLM, Sessions, Skills, files |
| `rlm/` | DSPy Signature, Root/Sub model roles, fresh RLM construction, delegation metrics, routing evaluation, fixed-depth child executor, options, events, runner | DSPy and domain values |
| `optimization/` | trusted-host GEPA/evidence lane | Daytona evaluator, evidence types |
| `daytona/` | exclusive SDK boundary: async platform/provisioning, provider-only Session ownership, DSPy-only sync interpreter seam (injected `SyncBridgeDispatcher`), confirmed Sandbox Lease ownership (`sandbox_lease.py`) with typed cleanup receipts and confirmed deletion lifecycle (`lifecycle.py`), pure broker source plus transport, diagnostics, Session Workspace gateways, and the mounted Workspace Memory adapter | Daytona SDK and domain values |
| `runtime/` | provider-neutral Sandbox binding records and store ports | domain values |
| `sessions/` | Session catalog, Turn input/history, canonical AssistantPart vocabulary, versioned Committed Turn | domain values |
| `files/`, `artifacts/` | Attachment staging, paged/full-lifecycle Session Workspace and Project tools, workspace-wide Memory values/tools, direct Workspace Artifact Candidate staging, Artifact promotion/read | storage interfaces and safe paths |
| `skills/` | bundled catalog, authorization, progressive loading, capability seam, Tool construction | domain values and package resources |
| `persistence/` | SQLAlchemy models and internal Run codec, claim decisions, liveness, final-state, query helpers, and repository adapters | Session/file/Artifact interfaces |
| `observability/` | sanitized failure diagnostics; opt-in Databricks MLflow DSPy tracing | domain errors, Settings |
| `cli/` | supervised Daytona plus pi-tui, backend launchers, doctor dispatch | ASGI entrypoint and Daytona diagnostics |

## Hard boundaries

- Daytona SDK imports are confined to `fleet_rlm.daytona`.
- `create_app()` may construct only the static Skill catalog/authorizer and empty
  capability registry in addition to the FastAPI shell. Lifespan owns runtime
  repositories, engines, LMs, providers, rollback, and cleanup.
- Routes retrieve runtime modules through dependency aliases. The Skills route
  may recreate only the static in-memory catalog fallback.
- `RLMRunner` owns execution, not Turn Commit or resource release.
  `RunLifecycle.finish()` owns result/Artifact publication and atomic commit;
  `TurnCoordinator` owns terminal ordering and final cleanup.
- `RunLifecycleService` maps outcomes to typed Claim commands, and both Run
  repositories apply them through one `transition_claim()` seam under their
  existing lock/transaction boundaries.
- `RuntimeInventory` publishes the complete dynamic route-facing graph only after
  validation; `settings` and the static Skill catalog remain app-level. Lifespan
  detaches the inventory before disposing its closeable resources.
- `DaytonaRuntimeResources` owns provider resources only;
  `composition/daytona.py` injects database, binding, model, preparation, limits,
  and cleanup ports.
- `chat/run_execution.py` owns the private post-preparation Run state machine;
  `TurnCoordinator` remains the public stream facade and `RunLifecycle.finish()`
  remains the artifact/atomic-commit owner.
- `daytona/broker_source.py` owns pure broker source generation;
  `http_broker.py` owns HTTP-in-sandbox transport and host-tool/SUBMIT lifecycle.
- Startup recovery claims eligible nonterminal rows by ownership. Daytona startup
  supplies a bounded provider fence; deterministic compositions supply an explicit
  no-provider fence policy, and failed fences restore retryable ownership.
- Daytona Admission bounds acquiring or active Interpreter Leases, not retained
  Session Sandboxes. Released Session Sandboxes are stopped after the explicit
  five-minute idle policy and restarted on the next acquisition.
- Independent Workspace access mounts exactly `workspaces/<workspace_id>` in a
  purpose-labelled ephemeral I/O Sandbox and exposes only its `files/` root.
- During Daytona Turn execution, Workspace Memory uses the fixed
  `memory/MEMORIES.md` under the already workspace-scoped mount (legacy root
  `MEMORIES.md` migrates on first open). v3 ids are fresh and persist; v1 ids
  are synthesized from canonical text plus valid-record occurrence for paging
  and upgrade to v3 on edit. Duplicate persisted ids fail closed, and
  edit/forget run their read-modify-publish rewrite in one mounted agent
  operation. It is distinct from
  Session Workspace under `sessions/{session_id}/workspace/` and Run state under
  `sessions/{session_id}/runs/{run_id}/`.
- In-memory and SQL repositories apply the pure `chat/run_claim.py` transition
  policy inside their respective lock and transaction boundaries.
- Alembic owns live schema evolution. `create_tables` is limited to explicit
  SQLite test/local helpers.
- Runtime Events are transport-neutral. `api/sse.py` alone owns the public AI
  SDK UI v1 SSE projection.
- `make api-sync` owns root OpenAPI, generated TUI HTTP types, and
  `tools/fleet-tui/src/generated/fleet-ui-chunk-validation.ts`.

The authoritative route inventory and shapes are in
[HTTP API](http-api.md) and `openapi.yaml`.

## Maintained terminal client

| Module | Ownership |
| --- | --- |
| `src/cli-core.ts` | CLI options and verified atomic Artifact download |
| `src/fleet-api-client.ts` | HTTP requests and response handling |
| `src/fleet-turn-stream.ts` | request opening, bounded same-key retry, strict stream and part lifecycle |
| `src/sse.ts` | SSE framing and closed validation against generated chunk tables |
| `src/tui/runner.ts` | active Run state, submission, cancellation |
| `src/tui/live-projection.ts` | live SSE chunk → store events (`LiveTurnProjector`) |
| `src/tui/durable-projection.ts` | durable reload turns → store events (`projectDurableTurns`) |
| `src/tui/projection-helpers.ts` | shared pure helpers / message builders for projection |
| `src/tui/store.ts` | conversation state, atomic Session hydration, and terminal stream settlement |
| `src/tui/application.ts`, `screen.ts`, `transcript.ts` | pi-tui alternate-screen lifecycle, editor/input, follow-end `ScrollView` layout, terminal-safe status, and mutable Run activity |
| `src/tui/message-renderer.ts`, `terminal-text.ts` | complete event, cached Markdown, result, Artifact, code/output presentation, and terminal-safe text |
| `src/tui/commands.ts`, `command-presenter.ts`, `autocomplete.ts` | slash commands, overlays, status, and completion |
| `src/generated/openapi.ts`, `src/generated/fleet-ui-chunk-validation.ts` | generated HTTP types and chunk-validation tables owned by `make api-sync` |

Live and reload use the same display semantics. There is no classic renderer.
The operator timeline renders in an alternate-screen follow-end `ScrollView`:
PgUp/PgDn/Home/End and the mouse wheel scroll, drag selects text for copy, and
tool/code/output cards fold with Ctrl+O.
