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
| `chat/` | `TurnCoordinator` claim-to-cleanup orchestration, Turn preparation, lifecycle finalization, owned post-commit Memory promotion, shared heartbeat/Claim policy | RLM, Sessions, Skills, files |
| `rlm/` | DSPy Signature, Root/Sub model roles, Session-scoped RLM construction/reuse with fresh per-Turn history, delegation metrics, routing evaluation, fixed-depth child executor, options, events, and native execution orchestration | DSPy and domain values |
| `optimization/` | trusted-host GEPA/evidence lane | Daytona evaluator, evidence types |
| `daytona/` | exclusive SDK boundary: async platform/provisioning, provider-only Session ownership, DSPy-only sync interpreter seam (injected `SyncBridgeDispatcher`), confirmed Sandbox Lease ownership (`sandbox_lease.py`) with typed cleanup receipts and confirmed deletion lifecycle (`lifecycle.py`), one contracted recursive-child owner covering acquisition, lease close state, strict cleanup, and late ownership (`recursive_child_runtime.py`), versioned installed Workspace Agent protocol with one packaged `handle(request)` artifact for installed and fallback launchers (`workspace_agent/`), canonical broker source plus transport, diagnostics, Session Workspace gateways, and the mounted Workspace Memory adapter | Daytona SDK and domain values |
| `runtime/` | provider-neutral `OwnedEffect` settlement primitive plus Sandbox binding records and store ports | domain values |
| `sessions/` | Session catalog, Turn input/history, canonical AssistantPart vocabulary, versioned Committed Turn | domain values |
| `files/`, `artifacts/` | Attachment staging, paged/full-lifecycle Session Workspace and Project tools, workspace-wide Memory values/tools, direct Workspace Artifact Candidate staging, Artifact promotion/read | storage interfaces and safe paths |
| `skills/` | bundled catalog, authorization, progressive loading, capability seam, Tool construction | domain values and package resources |
| `persistence/` | SQLAlchemy models and internal Run codec, claim decisions, liveness, final-state, query helpers, Memory promotion outbox (P23), and repository adapters | Session/file/Artifact interfaces |
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
- `rlm/runtime.py` keeps `RLMRunner.stream(context)` as the deep execution seam.
  It owns the cancellation-shielded worker/thread/event-loop boundary, bounded
  detail relay/monitoring/drain policy, and trace/recursive-metric projection
  via `rlm/events.py`. These are private implementation modules, not new public
  orchestration surfaces.
- `runtime/owned_effect.py` defines the one provider-neutral wait vocabulary for
  already-started async effects: caller cancellation is shielded, an optional
  bounded wait never cancels the effect, and terminal errors remain surfaced.
  Run lifecycle, RLM worker, and equivalent provider waits use it; recursive
  batch futures and Daytona lease/quarantine state retain their specialized
  ownership and cleanup policies.
- `daytona/memory_diagnostics.py` owns the bounded P31 degradation taxonomy:
  normalization, provider unavailability, corrupt records, invariant
  violations, search failure, legacy migration, and unexpected internal
  failures. The optional read-side Memory path stays fail-soft; mutations and
  list operations remain fail-closed.
- `tools/fleet-tui/src/tui/tests/turn-reducer-invariants.test.ts` is the P32
  deterministic convergence proof. Live and durable adapters may differ in
  framing, but both reduce through the same canonical event vocabulary and
  source-agnostic reducer.
- `RunLifecycleService` maps outcomes to typed Claim commands, and both Run
  repositories apply them through one `transition_claim()` seam under their
  existing lock/transaction boundaries.
- `RuntimeInventory` publishes the complete dynamic route-facing graph only after
  validation; `settings` and the static Skill catalog remain app-level. Lifespan
  detaches the inventory before disposing its closeable resources.
- `DaytonaRuntimeResources` owns provider resources and exposes the public
  `DaytonaRuntime` root/child lifecycle;
  `composition/daytona.py` injects database, binding, model, preparation, limits,
  and cleanup ports.
- `TurnCoordinator` owns the private claim-to-cleanup Run state machine and
  public stream facade. `RunLifecycle.finish()` remains the Artifact/atomic-commit
  owner, while `RunCleanupSupervisor` is only a bounded cleanup fallback.
- `daytona/broker.py` is the sole owner of broker source generation,
  HTTP-in-sandbox transport, host-tool/SUBMIT lifecycle, and the injected
  synchronous DSPy bridge.
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

The final P34 ownership matrix, freeze constraints, and certification commands
are maintained in [Maintainability freeze](../how-to-guides/maintainability-freeze.md).

## Maintained terminal client

| Module | Ownership |
| --- | --- |
| `src/cli-core.ts` | CLI options and verified atomic Artifact download |
| `src/fleet-api-client.ts` | HTTP requests and response handling |
| `src/fleet-turn-stream.ts` | request opening, bounded same-key retry, strict stream and part lifecycle |
| `src/sse.ts` | SSE framing and closed validation against generated chunk tables |
| `src/tui/runner.ts` | active Run state, submission, cancellation |
| `src/tui/canonical.ts` | canonical semantic Turn event types (backend-mirrored) + cross-language JSON serializer |
| `src/tui/live-adapter.ts`, `durable-adapter.ts` | thin wire → canonical adapters (all casing/wrapper compat lives here) |
| `src/tui/turn-reducer.ts` | one source-agnostic reducer: canonical events → store events (all fold/mint state) |
| `src/tui/live-projection.ts`, `durable-projection.ts` | stable facades composing adapter + reducer for the runner/hydration call sites |
| `src/tui/projection-helpers.ts` | shared pure helpers / message builders for projection |
| `src/tui/store.ts` | conversation state, atomic Session hydration, and terminal stream settlement |
| `src/tui/application.ts`, `screen.ts`, `transcript.ts` | pi-tui alternate-screen lifecycle, editor/input, follow-end `ScrollView` layout, terminal-safe status, and mutable Run activity |
| `src/tui/message-renderer.ts`, `terminal-text.ts` | complete event, cached Markdown, result, Artifact, code/output presentation, and terminal-safe text |
| `src/tui/commands.ts` (facade), `src/tui/commands/` (`registry.ts`, `shared.ts`, `sessions.ts`, `skills-settings.ts`, `files-artifacts.ts`, `status-theme-misc.ts`), `autocomplete.ts` | slash command registration, parsing, handlers in stable `/help` order, and completion |
| `src/tui/command-presenter.ts` (facade), `src/tui/presenter/` (`overlay.ts`, `settings.ts`, `skill-selector.ts`) | interactive overlays, settings editors, and skill selector |
| `src/generated/openapi.ts`, `src/generated/fleet-ui-chunk-validation.ts` | generated HTTP types and chunk-validation tables owned by `make api-sync` |

Live and reload use the same display semantics. There is no classic renderer.
The operator timeline renders in an alternate-screen follow-end `ScrollView`:
PgUp/PgDn/Home/End and the mouse wheel scroll, drag selects text for copy, and
tool/code/output cards fold with Ctrl+O.
