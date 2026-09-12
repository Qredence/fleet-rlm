# Fleet RLM Architecture

## Purpose

Fleet RLM is a durable, Session-first conversational system. A FastAPI
transport accepts a user Turn, the native DSPy RLM performs bounded work with
Daytona-backed execution resources, and the result is settled into durable
Session state. The maintained operator client is the pi-tui application under
`tools/fleet-tui/`.

Canonical Run Environment set: `daytona`.

## Current runtime and migration target

`runtime.variant = "legacy"` is the only selectable architecture. It uses native
DSPy RLM with Fleet's broker-backed interpreter. Production execution builds a
fresh DSPy program, direct Tool bindings, callbacks, and worker executor for
each Run. Sequential successful Turns may reuse the broker Root Sandbox;
invocation history, authority, budgets, and bindings remain Turn-local.
Failures and taint force rotation before reuse. Cross-Turn correctness depends
on committed history, PostgreSQL, and the Workspace Volume rather than a
resident Python/DSPy object graph.

Native Daytona interpreter cutover is not on the production path: context
deletion does not contain detached process-session children, and the native
worker/lease branch has been removed. Turn preparation and the RLM runner
accept only retained broker execution. Fleet child RLM tools (`rlm_query` /
`rlm_query_batched`) are opt-in: `[defaults.rlm] recursion_enabled = false`
in the shipped policy. Native `llm_query` remains the semantic
delegation path. The [ADR 006 status ledger](docs/decisions/006-implementation-status.md)
owns dated evidence and remaining gates; this page describes current ownership.

## System model

```text
User / Workspace
  -> Session
  -> Turn
  -> Run claim and preparation
  -> native DSPy RLM execution
  -> Runtime Events over the public stream
  -> settlement, Artifact promotion, and Turn Commit
  -> durable projections and replay
```

A User is the deterministic local actor namespace. A Workspace owns Sessions,
Skills, Attachments, Artifacts, and workspace-scoped state. A Session owns
ordered Turns and committed history. A Turn is one user message; a Run is its
execution attempt. A successful Turn is not complete until durable settlement
and Turn Commit succeed.

## Component ownership

### API

`src/fleet_rlm/api/` owns HTTP identity and validation, dependency aliases,
schemas, routes, OpenAPI derivation, and SSE projection. Routes are transport
adapters: they obtain runtime services through composition and dependency seams
and do not construct stores, engines, models, or provider clients. The SSE
layer projects typed, transport-neutral Runtime Events into the public client
stream. Fleet exposes no `/api/v1`, WebSocket execution, compatibility aliases,
or graphical frontend contract.

The FastAPI HTTP/SSE service is the canonical execution interface. `fleet web`
and `fleet-rlm serve-api` launch that service; `fleet cli` supervises the same
backend and attaches the pi-tui client. A launcher may select a validated
non-secret TOML profile explicitly with `--profile NAME`; omitted selection
retains `config.default_profile`. Explicit profile loading occurs before
provider, database, Daytona, or client initialization, and explicit profiles
are incompatible with Uvicorn `--reload`.

Campaign transport design and the historical partial-run receipt are documented
in [evaluation and monitoring](docs/how-to-guides/evaluation-optimization.md#phase-4-transport-evidence).
The [ADR 006 ledger](docs/decisions/006-implementation-status.md) owns subsequent
campaign results and certification status.

### Composition

`src/fleet_rlm/composition/` constructs the process-scoped runtime graph. The
FastAPI lifespan validates settings, publishes one complete runtime inventory,
and owns startup rollback, detachment, and shutdown. Live Daytona wiring and
the credential-free deterministic test composition are separate composition
paths; the test path is not a public fallback runtime.

### Chat and Turn orchestration

`src/fleet_rlm/chat/` owns Turn preparation, Run claiming, stream
orchestration, lifecycle coordination, terminal ordering, and cleanup. The
preparer validates ownership and inputs, acquires the resources needed for the
Run, and rolls them back in reverse order on failure. `RunLifecycle.finish()`
owns typed-result validation, private result snapshots, Artifact Candidate
promotion, durable failure settlement, and atomic Turn Commit. `TurnRuntime`
and its coordinator own stream settlement, heartbeats, cancellation, and final
resource cleanup.

### RLM runtime

`src/fleet_rlm/rlm/` owns Fleet's DSPy Signature inputs, process-scoped model
templates, fresh per-Run program construction, per-Turn bindings, native
options, Runtime Events, trajectory reconciliation, semantic query tools, and
the opt-in recursive child executor.

The execution levels are distinct:

- DSPy RLM iterations are bounded action/REPL steps within one native RLM.
- Native semantic LM calls are `llm_query` or `llm_query_batched` prompts made
  through the configured Sub model and counted by the native call budget.
- Fleet recursive delegation uses `rlm_query` or the Root-only
  `rlm_query_batched` to run isolated iterative child RLMs.

The Root begins at native depth zero. Direct recursive children run at depth
one and receive no Fleet recursive tools; further semantic work uses native
DSPy calls. Root exposes only `rlm_query(capsule=...)` and
`rlm_query_batched(capsules=...)`. Child batches preserve input order and reserve
shared budgets atomically. Ordinary fully cleaned-up failures return typed
partial outcomes; cancellation, authorization, and cleanup failures remain
fatal. Children return evidence for Root verification and synthesis, not final
authority.

Recursive work is scheduled on the injected application event loop under one
Turn-owned semaphore. The scheduler creates neither child event-loop threads
nor a separate batch thread pool. Native child `dspy.RLM.forward` runs on an
owned blocking worker because the pinned async RLM invokes its interpreter
synchronously. Cancellation does not release worker ownership before settlement;
the existing Root worker execution boundary is unchanged.

Selected-input capsules use byte-accurate allocations and typed child results.
Their read-only input reader reuses prepared Session/Project text capabilities,
checks the cumulative byte allowance and supplied checksum, and reports only
successfully accessed reference identifiers. Inline fragment delivery is tracked
separately. Child answers cite canonical `[reference-N]` or `[fragment-N]`
identifiers. Fleet rejects undeclared/unread citations and omitted required
citations, returning validated `cited_evidence` separately from the access
ledger. These identifiers prove access, not that a claim is true. Selected
`artifact://<UUID>` locators use the Turn-bound ArtifactReader for authorization
and checksum verification, with the remaining byte allowance checked before
fetching content. Reads run on the application loop; an owned child worker waits
for actual read settlement, and revoked authority prevents late delivery. An
Artifact locator never grants independent storage authority. Recursive execution
does not build a Session snapshot, copy committed history, or construct a
nested Fleet executor. The existing Root history boundary is unchanged.

DSPy creates invocation-local `REPLHistory` and owns trajectory semantics.
Fleet supplies committed `dspy.History` and Turn-local bindings without
independently compacting, truncating, resetting, or reconstructing native
`REPLHistory`. Process-scoped LM instances are immutable templates. Deadlines,
callbacks, adapters, retries, tools, and other mutable execution state are
bound per Turn or child invocation.

### Daytona

`src/fleet_rlm/daytona/` is the exclusive Daytona SDK boundary. It owns
provider lifecycle, Sandbox and Interpreter Lease operations, mounted Volume
access, the injected synchronous DSPy interpreter seam, recursive child
resources, Workspace Agent protocol, broker transport, and provider-error
normalization. Provider exceptions and raw infrastructure details do not cross
this boundary into routes or public events.

Each Run holds its Interpreter Lease through finalization. Recursive children
receive fresh isolated Sandboxes and child-scoped Volume paths. Cleanup is
owned, deadline-bounded, and re-observed; no detached provider work may mutate
Fleet state after settlement.

### Workspace and persistence

`src/fleet_rlm/sessions/`, `workspace/`, `attachments/`, `artifacts/`, and
`persistence/` own provider-neutral domain policy and durable adapters.

Session History and Checkpoints are durable semantic state. Attachments and
Artifact bytes live in Workspace Volume Scope. Artifact Candidates remain
private until byte validation, promotion, and Turn Commit. Session Workspace
files and Workspace Memory are immediate private state and are intentionally
separate from committed conversation history. Memory writes are explicit,
bounded, and host-mediated.

The independent Workspace `files/` namespace is exposed through `/api/files`;
Session Workspace scratch and `projects/<slug>/` deliverables have distinct
host-tool namespaces. Explicit memory appends persist immediately. The optional
`propose_memory` capability is absent under the shipped empty category allowlist;
when enabled, its candidates enter a durable post-commit promotion outbox.

Alembic owns live schema evolution. Explicit SQLite test/local helpers may
create tables, but production startup does not use `create_all`. In-memory and
SQL Run repositories share the same typed claim-transition policy while
retaining their respective lock or transaction boundaries.

### Configuration

`config/fleet.toml` is the canonical runtime policy. `[defaults]` is deep-merged
with the selected `[profiles.<name>]` table. The loader validates the complete
policy at startup, and only environment names explicitly referenced by the
selected policy may supply secrets or endpoints. Ambient selectors and
unreferenced environment variables do not override policy.

The loopback-only settings API edits non-secret policy for a later restart.
Settings responses may expose environment-variable names and policy metadata,
never the referenced values. A policy change does not mutate active runtime
composition or in-flight Turns.

### Observability

`src/fleet_rlm/observability/` owns sanitized diagnostics, optional MLflow
tracing, PostHog analytics, and DSPy callback projection. Observability is
fail-soft: unavailable tracing or analytics cannot change execution outcomes,
settlement, or public success/failure semantics. Trace and event payloads are
bounded and sanitized; credentials, tokens, connection strings, URLs, and
private paths are never exported. Authorized MLflow traces may include bounded
reasoning and system-prompt content under the MLflow content policy, while
public Runtime Events and SSE payloads keep their separate content boundary.

### TUI

`tools/fleet-tui/` is the maintained pi-tui client. It consumes the generated
HTTP types and the backend's public SSE contract; it owns no model, provider,
or execution lifecycle. The stream client validates framing and terminal
ordering, live and durable projections converge through the client reducer,
and presenters own interaction rather than backend semantics. Its specialized
tooling and validation rules live in `tools/fleet-tui/AGENTS.md`. It is an
operator-facing client of the canonical FastAPI service, not a campaign driver
and not a second runtime.

## Dependency boundaries

The intended direction is:

```text
API transport
    ↓
application and lifecycle orchestration
    ↓
provider-neutral domain/runtime
    ↓
provider and persistence adapters
```

Composition is the wiring seam between these layers. Daytona-specific SDK
imports stay in `src/fleet_rlm/daytona/`; provider-neutral runtime and domain
modules depend on interfaces or typed ports instead. Routes depend on
composition-provided services, not concrete infrastructure. Runtime Events
remain transport-neutral until the API SSE adapter projects them.

## Lifecycle and ownership invariants

- A Run is claimed before useful preparation or execution begins.
- `RunLifecycle.finish()` is the only owner of successful durable settlement,
  Artifact publication, result snapshots, and Turn Commit.
- `TurnRuntime` owns terminal ordering and final cleanup; the RLM runner never
  emits a terminal event or commits durable Session state.
- Interpreters, Sandboxes, child workers, and post-commit work have explicit
  owners and remain owned until deadline-bounded cleanup settles.
- No cancellation, timeout, claim loss, or provider failure may publish a
  successful Committed Turn or Artifact identity.
- Process-scoped templates are immutable; mutable execution state is Turn- or
  child-scoped and cannot leak across concurrent Runs.
- Public Runtime Events and durable projections are closed contracts; transport
  adapters do not become a second source of execution truth.
- Provider credentials, private paths, and raw provider failures never enter
  public or trace surfaces. Only approved bounded Root reasoning/code/output
  projections reach SSE/TUI; engineering trace content has a separate policy.
- Bytes are validated before metadata is published, and Alembic remains the
  live schema authority.

## DSPy RLM contract

The historical migration target uses a fresh InterpreterContext per Turn and distinguishes
Volume-less SemanticChild from restricted-data WorkspaceChild. See
[ADR 004](docs/decisions/004-turn-interpreter-context.md). These are target
contracts; the Phase 1 decision retains broker execution and existing Session
reuse while resident-state subtraction remains open. Native execution is not
a pending selectable alternative. [ADR 005](docs/decisions/005-runtime-variant.md)
defines the single execution-architecture selector.

Fleet uses the repository-pinned DSPy implementation as the behavioral source
of truth. A native RLM invocation receives the declared request, committed
history, bounded Session context, authorized Skill metadata, and bounded
Attachment metadata. The caller owns the interpreter passed to DSPy and Fleet
projects bounded callback, trajectory, code, output, and tool evidence into
Runtime Events.

`max_iters`, `max_llm_calls`, and `max_output_chars` govern native RLM work;
they are not recursive-depth controls. Recursive depth is a Fleet execution
boundary: one direct native child level, then Sub-LM fallback. Root-only
recursive batches reserve child capacity before launching siblings, preserve
order, and join every owned worker before releasing the Run lease.

One absolute Turn deadline fences provider calls, semantic queries, child
admission, joins, and useful work. Caller cancellation is not permission to
detach cleanup or release a resource still owned by the Run.

## Generated contracts

`openapi.yaml` is derived from the backend API models. The TUI HTTP types and
stream/chunk validation tables are generated artifacts. Change their source
models or generation logic, then run:

```bash
make api-sync
make api-check
```

Generated files are reviewed as public contract changes and are never edited
by hand.

`make stream-sync` / `make stream-check` own the deterministic TUI stream
fixture. `make profile-matrix` regenerates the policy-derived profile reference;
`make check-docs` verifies it. Bundled Skill Markdown is loaded into model context
and must retain valid manifests, resource paths, and catalog contracts.

## Validation architecture

Executable contracts are the primary enforcement mechanism. Tests cover domain
behavior, lifecycle races, persistence parity, public HTTP/SSE shapes, and
TUI convergence. `make api-check` catches generated-contract drift;
`make check-codebase-tree` and `make check-dependency-boundaries` enforce
structural ownership; Python and TypeScript type/lint/format checks enforce
implementation hygiene; and `make check-docs` / `make check-release` validate
documentation, configuration, script, and repository guidance surfaces.

[AGENTS.md](AGENTS.md#validation-selection) owns validation selection and live-run
authority; the [testing strategy](docs/how-to-guides/testing-strategy.md) describes
suite mechanics.
