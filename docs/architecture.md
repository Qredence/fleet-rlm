# Fleet RLM backend architecture

Canonical Run Environment set: `daytona`.

## Runtime flow

```text
POST /api/sessions/{session_id}/turns + Idempotency-Key
  -> deterministic local scope and Turn input validation
  -> Attachment ownership and exact Skill selection validation
  -> TurnCoordinator.open()
     -> RunLifecycle.begin(): replay or atomic Run claim
     -> DefaultRunPreparer.prepare(): context, tools, environment resources
  -> RLMRunner: one compatible native dspy.RLM/interpreter per resident Session
     runtime, with fresh per-Turn bindings and DSPy REPLHistory
  -> Runtime Events from native trajectory, interpreter, and host-tool boundaries
  -> RunLifecycle.finish()
     -> validate typed result and private snapshot
     -> promote Artifact Candidate bytes (Daytona only)
     -> atomic Turn/Run/Checkpoint/Artifact commit or failure settlement
  -> artifact.created* then exactly one run.completed terminal
  -> TurnCoordinator cleanup and Interpreter Lease release
```

Malformed request bodies and structural schema failures remain ordinary safe
HTTP outcomes before headers. Claim, ownership, and preparation failures after
the stream opens emit closed `error` + `finish` chunks instead of late HTTP
responses. A failed commit advances no Session history, publishes no Artifact
identity, and still releases owned resources.

Within one resident Session runtime, interpreter calls reuse one context so
Python state persists across sequential clean Turns. Each Turn receives fresh
request/capability bindings, output metadata, budgets, and DSPy `REPLHistory`.
Failure, taint, idle eviction, or Sandbox replacement rotates the runtime;
committed History and Volume-backed state survive, while arbitrary Python globals
may not. A different Session never shares the runtime.

Every Signature receives request text, complete committed `dspy.History`, bounded
`session_context`, bounded `skill_cards`, and bounded Attachment metadata. The
History contains only canonical `{"request": ..., "answer": ...}` records from
the claimed checkpoint; hidden trajectory and failed Turns are excluded. The
existing `read_session_history` Tool remains for explicit retrieval and
compatibility. The default Signature uses strict local Pydantic DTOs; conversion
and JSON serialization happen once immediately before native
`await rlm.acall(interpreter, **named_inputs)`. Custom Skill Signatures retain
their JSON-compatible common input annotations.

`rlm/program.py` owns the default Fleet Root instruction fragments. Base,
REPL, tool, optional recursion, verification, and bounded-context guidance are
composed directly; disabling recursion no longer deletes text from one large
monolithic Signature docstring.

The Root follows a bounded delegation ladder: Python for deterministic work,
native `llm_query`/`llm_query_batched` for semantic work, `rlm_query` for one
iterative isolated subproblem, and Root-only `rlm_query_batched` for ordered
independent child RLMs. The Root starts at recursive depth 0; direct recursive
calls run as native depth-1 children, while a child’s further recursive call is
a bounded Sub-LM fallback with no grandchild Sandbox. Children receive copied
DSPy Root/Sub runtimes, an immutable committed Session History snapshot, and
bounded context; they return evidence for Root verification and synthesis.
Fleet reserves the shared recursive budget atomically and controls sibling
concurrency through `recursion_max_parallel_children`.

## Composition and ownership

- `app.create_app()` creates the FastAPI application, installs handlers and
  routers, and eagerly constructs the immutable bundled Skill catalog.
- FastAPI lifespan validates settings and installs exactly one complete Daytona
  or explicitly injected private-test runtime inventory. It owns
  startup rollback and shutdown.
- `composition/common.py`, `daytona.py`, and `testing.py` own runtime wiring. A
  locally owned database engine creates tables only for SQLite.
  Import `fleet_rlm.composition.testing` directly in tests; it is not re-exported
  from `composition` and is never installed by lifespan.
- Routes retrieve composed runtime modules through `api/dependencies.py`; the
  Skills discovery route may recreate only its static in-memory catalog fallback.
- `DefaultRunPreparer` owns ordered validation, environment acquisition,
  bounded context, Tool construction, and reverse-order rollback.
- `AttachmentLifecycleService` owns Attachment upload, authorization, integrity,
  and Run staging; it is not a DSPy execution Module.
- Daytona composition constructs Turn preparation explicitly; process-lifetime
  `DaytonaRuntimeResources` owns provider resources but no mutable preparation graph.
- Daytona uses one process-owned `AsyncDaytona` for provisioning, lifecycle,
  filesystem, and Workspace operations. Only DSPy's synchronous
  `CodeInterpreter.execute()` seam receives an explicit, allowlisted
  async-to-sync view, and it blocks only the DSPy worker thread.
  `WorkspaceVolumeGateway.open_workspace()` scopes each grouped I/O operation
  to one ephemeral Sandbox, which is deleted before the context exits.
- `RLMRunner` executes one compatible Session-scoped DSPy RLM and emits no
  terminal event. It binds current-Turn Tools through stable authorization-aware
  proxies and keeps the per-Session execution lane through durable settlement.
  The
  caller-owned interpreter observes ordinary stdout at the execution boundary,
  publishes bounded `RLMOutput` deltas with one per-step stream identity, and
  emits a final non-delta correction; trajectory reconciliation and durable
  Turn normalization retain one canonical output part.
- `RunLifecycle.finish()` owns private result snapshots, Artifact publication,
  atomic Turn Commit, durable failure settlement, and the bounded handoff to
  owned post-commit Memory promotion. Promotion is settled before the Run lease
  and mounted resources release.
- `sessions/assistant_parts.py` owns the closed Pydantic AssistantPart
  vocabulary for durable assistant content. `CommittedTurnCodec` validates
  payloads through that discriminated union, while reload projection consumes
  the resulting canonical runtime parts; live SSE chunks remain separate
  transport contracts.
- `api/ui_stream.py` owns the typed discriminated live Fleet UI chunk union.
  `api/sse.py` validates each projected RuntimeEvent frame through those
  models before SSE serialization, `api/openapi.py` derives OpenAPI from the
  same models, and generated TUI types/validators remain checked artifacts.
- `RunLifecycleService` translates lifecycle outcomes into typed Claim commands;
  the in-memory and SQL Run state stores apply the same pure policy through one
  `transition_claim()` persistence operation. Successful `commit()` and
  `request_cancel()` remain separate atomic/authorization paths.
- `TurnCoordinator` owns stream orchestration, heartbeat coordination, terminal
  ordering, and final cleanup.
- `daytona/` is the exclusive Daytona SDK boundary.
- `persistence/` implements domain repository interfaces; Alembic owns the live
  schema. `persistence/repositories/run_codec.py` centralizes ORM/domain and
  durable JSON conversion inside the deep Run facade, while
  `run_claim_decisions.py` groups idempotency/active-Run fencing,
  `run_liveness.py` groups heartbeat, recovery ownership, provider fencing,
  cancellation marking, and tombstone persistence, `run_final_state.py` owns
  commit/settlement transitions, and `run_queries.py` owns replay/history
  projections. The in-memory and SQL Run
  state stores retain lock-backed and transaction-backed atomicity and the
  pure Run Claim transition policy.

## Skills

The bundled catalog contains `dspy-rlm`, `long-context`, `workspace-files`,
`data-analysis`, and `report-builder`. Skill disclosure is progressive: bounded
Cards are the startup discovery surface. Without explicit selections, the RLM
receives the full bundled catalog and may load up to four advertised Skills
during the Turn. Exact version-pinned selections instead advertise, preload,
and restrict the Turn to only the authorized selected cards. A full `SKILL.md`
loads only when invoked or exactly preselected, and declared resources load only
after the Skill body. `data-analysis` is the only bundled Skill that supplies a
custom validated DSPy Signature; `report-builder` and `dspy-rlm` are
instruction-only.

Skill Markdown and resources cannot register host tools. Runtime composition
owns the fixed core tools plus exactly `load_skill` and
`read_skill_resource`. HTTP requests provide only Skill identity/version
selections. Selection is resolved synchronously against the immutable catalog;
at most one selected Skill may provide a validated DSPy Signature.

Host tool event views expose bounded allowlisted metadata. A Tool without a
declared view exposes identity, name, status, and a fixed failure message only.
Provider and transport failures use closed public messages rather than raw
exception text.

## Runtime profiles

- Daytona owns Sandbox/Interpreter Leases, Workspace Volume Scope, durable
  Attachment staging, Session Workspace files, Workspace Memory, private result
  snapshots, and Artifact Candidate promotion.
- The Volume layout provisions only owned namespaces: shared attachments and
  artifacts, `memory/MEMORIES.md` (the legacy root `MEMORIES.md` migrates on
  first open), browsable `projects/<slug>/`, Session Workspace, Run attachments
  and candidates, committed Artifacts, and private `result.json`. Bundled
  Skills remain host-owned and are not copied into the Volume.
- Profiles are explicit and fail closed when prerequisites are absent. Their
  provider, token, recursion, and environment contracts are generated in the
  [profile matrix](reference/profile-matrix.md). Private deterministic testing
  composition is not a public fallback profile.

## Terminal client

`@earendil-works/pi-tui@0.84.2` is the only renderer. Fleet uses `TuiAltScreen`
with a follow-end transcript `ScrollView`; the client requires Node 22.19+, owns
no model or provider runtime, and consumes the FastAPI HTTP/SSE contract.

`fleet-turn-stream.ts` owns strict request/retry/stream lifecycle and part
ordering; `sse.ts` owns framing and generated-chunk validation; `tui/runner.ts`
owns active Run and cancellation control; `live-projection.ts` /
`durable-projection.ts` own live SSE and durable reload projection (shared
helpers in `projection-helpers.ts`); `store.ts` owns atomic hydration and terminal
stream settlement. The application, screen, message renderer, commands,
presenters, and autocomplete own interaction and static presentation.

The operator timeline renders in an alternate-screen follow-end viewport.
PgUp/PgDn scroll a page, Home/End jump top/bottom, the mouse wheel scrolls,
drag selects text for copy, and tool/code/output cards fold with Ctrl+O. There
is no classic renderer. Artifact CLI downloads validate content length and
SHA-256 before atomically replacing the requested destination.

## Durable files

Attachment bytes are written to Workspace Volume Scope before metadata and are
staged for referenced Runs. Artifact Candidates are private Run outputs until
verified bytes reach UUID-unique durable paths and their metadata commits with
the Turn. Failed metadata commits may leave GC-eligible orphan bytes, never
public rows.

Session Workspace files are immediate private state under the Session Volume
path. Daytona exposes bounded list/read pagination, append and in-place unique
fragment edits, whole-file replacement, and strict delete (files and empty
directories only; no recursion, no force flag). Writes, appends, edits, and
deletes accept optional SHA-256 preconditions and never follow symlinks. For
write and append, checksum comparison and mutation execute inside one mounted
Workspace agent operation with target locking and inode revalidation across
I/O Sandboxes; the host does not recheck after a separate read. Existing Workspace documents can be staged as
private Artifact Candidates without resending their bodies; Turn Commit remains
the only publication boundary. Workspace files survive failed Runs and Sandbox
replacement independently of the commit-gated result snapshot and Artifact
lifecycle.

Daytona Workspace Memory is separate workspace-wide immediate state. Its fixed
`MEMORIES.md` lives at `memory/` under the already mounted
`workspaces/<workspace_id>` Volume subpath (a pre-existing root `MEMORIES.md`
is migrated there on first open, never losing content); Session and Run state
retain their nested paths below that root. The RLM accesses memory through
`read_workspace_memory`, `remember`, `list_memories`, `search_memories`, `edit_memory`, and
`forget` (plus the `update_workspace_memory` back-compat alias); Fleet also
injects a bounded, tolerant <= 4 KiB `workspace_memory tail` digest of
relevant plus newest records into the Turn's `session_context` so query-relevant
and recent learnings are available without a Tool call. Private deterministic tests use an unavailable
Workspace capability unless they explicitly inject a host-owned test
capability.

`remember` appends one complete provenance-aware v3 record with a fresh id,
limited to 4 KiB of formatted UTF-8, and becomes durable immediately,
independently of Turn Commit. A repeated identical record is idempotent. Legacy
v1 rows derive a deterministic id from canonical text plus valid-record
occurrence, so duplicate legacy rows remain separately pageable; duplicate
persisted ids fail closed rather than skipping or selecting an arbitrary row.
Expansion parsing also admits provenance-aware v3 rows (`id/source/updated` and
optional `supersedes` metadata). Explicit-user `remember` writes are now v3,
while historical v1/v2 remain readable; invalid v3 metadata stays malformed
under tolerant reads. A valid edge can target only one existing active memory
and cannot self-reference or cycle. Chronological history remains visible, but
`search_memories` and the automatic digest use the active-memory view, so a new
record can hide its superseded target without deleting history; forgetting a
superseder reactivates the old active memory.

Autonomous learning is disabled by default through
`rlm.autonomous_memory_categories = []`. When the selected policy explicitly
allows one or more canonical categories, the Root may see `propose_memory`;
otherwise the Tool and event view are absent. A proposal records only an
immutable, bounded `agent_candidate` learning plus category and optional
memory-ID target in host Run state. It performs no Workspace Memory, Volume, or
database write, recursive children cannot call it, and Tool events project
category/count/byte metadata rather than learning text. Failed, cancelled,
timed-out, or settlement-failed Runs discard the proposal without a memory
write. After a successful durable assistant-Turn commit, lifecycle settlement
may best-effort-promote valid candidates as v3 `source:agent_candidate` rows:
category policy and active memory content are rechecked, exact active category
plus learning content dedupes, and only an active durable supersession target
is honored. Post-commit promotion failure records a bounded warning/trace
outcome and never rolls back or edits the committed Turn. Replay does not
re-run proposal or promotion. This delivery is post-commit best-effort, not an
exactly-once crash-recovered side-effect ledger.

`edit_memory`
upgrades legacy rows to v3 or replaces v3 while preserving the id and
timestamp, and `forget` removes exactly one addressed row. `remember`, edit,
delete, and legacy migration run their read/validate/compose/publish sequence
inside one mounted-agent operation behind a stable, zero-byte `memory/MEMORIES.md.lock` advisory lock and inode/path revalidation. The host
process-local thread lock is only an optimization; independent host processes
whose mounted agents execute on one lock-honoring Sandbox kernel coordinate
through that mounted lock (the lock inode opens `O_RDWR|O_CREAT`; read-only
lock creates are rejected EPERM on WORM-like Volume backends). Mutation
publication degrades in capability order: atomic `os.replace` publish,
direct `O_TRUNC` overwrite, then unlink-and-recreate for Volume backends that
reject both rename/link and every in-place write-open on existing files
(Fall 2026 live probe: EPERM on `O_WRONLY`/`O_RDWR`/`O_APPEND`/`O_TRUNC` opens
of existing entries; ENOSYS on rename/link). A failed recreate restores the
previous bytes before the bounded error surfaces, so a failed mutation never
silently destroys the log. Cross-Sandbox FUSE lock semantics remain part of
the live P16 certification surface and receive no stronger claim here. The
sidecar is control metadata, never a memory record or digest input. A
completed append therefore survives failed or cancelled Turns and Sandbox
replacement. The update Tool permits writes only when the user explicitly
requests memory, but that is an auditable Tool policy rather than a filesystem
ACL: the Daytona interpreter can see the mounted Volume.

Reads return the newest complete records within a fixed 256 KiB byte budget.
The configured `max_upload_bytes` caps the whole memory file. Appends against a
full or torn file fail closed, as does access to unsafe or invalid storage;
reads omit an incomplete trailing record, and appends never repair or rewrite
one. Fleet performs no automatic compaction; deletion and edits require their
explicit Tools. Every Turn receives the bounded memory digest described above,
while full history stays behind the read/list Tools.
Memory calls use generic Tool events whose allowlisted metadata excludes
learning bodies, provider paths, and raw errors; there is no dedicated memory
event. The live cross-Sandbox, cross-Session Workspace Memory proof remains
gated and has not been run.

## Compatibility and status

The maintainability baseline is frozen by the [P34 certification guide](how-to-guides/maintainability-freeze.md).
It records the one-owner seams for lifecycle settlement, native RLM
observation, recursive child cleanup, the Workspace agent, Memory diagnostics,
configuration metadata, and canonical TUI reduction. The freeze is structural:
it does not authorize model-routing, latency, throughput, schema, Memory-format,
or public HTTP/SSE changes.

There is no legacy backend, `/api/v1`, WebSocket execution, dual-serve, data
migration layer, classic terminal renderer, or maintained Web frontend. A
future graphical client is a separate effort. The current module ownership is
also summarized in the [codebase map](reference/codebase-map.md). The
integrated P41 delivery freezes public behavior per the
[P41 behavior freeze](reference/behavior-freeze.md); the freeze binds
behavior, never private Python structure.
