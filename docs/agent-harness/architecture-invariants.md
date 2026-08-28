# Architecture Invariants

If a change violates an invariant, remediate the code or update this document
and its matching automated check in the same patch.

## Foundations (frozen at ownership-architecture integration, P25)

- **DSPy is Fleet's foundational cognitive framework, not a compatibility
  layer.** Each compatible resident Session runtime owns one native `dspy.RLM`
  and caller-owned interpreter; sequential clean Turns reuse them while each
  invocation receives fresh DSPy `REPLHistory` and Turn bindings. Tools are
  `dspy.Tool` objects, and execution goes through the caller-owned
  `await rlm.acall(interpreter, **named_inputs)` surface under the pinned DSPy
  3.3.1 contract. `RLMOptions.max_iters`
  enforces DSPy's iteration budget end-to-end; native `max_llm_calls` and
  `max_output_chars` retain their DSPy meanings. Bounded child RLMs provide
  controlled width at fixed native depth one. Pinned framework constructor,
  Signature, Prediction, Tool, Root/Sub LM, callback, trajectory, usage, and
  child-RLM contracts are regression-gated in `tests/unit/backend/rlm/` and the
  live matrix.
- **SQLite owns Sessions, Runs, Turn/Artifact records, sandbox bindings, and
  the Memory promotion outbox.** Alembic owns the live schema (fresh canonical
  baseline plus chained revisions); `create_tables` is reserved for explicit
  test/offline helpers. The outbox rides the same transaction as successful
  Turn commit; it carries recovery state only, never authoritative Memory
  content.
- **The Daytona Volume is the durable authority for Workspace bytes:**
  Attachments, promoted Artifacts, result snapshots, and the Workspace Memory
  log. Sandboxes are replaceable compute attached to that Volume; retained
  Session Sandboxes own capability state, and ephemeral I/O Sandboxes are
  purpose-labelled, verified, and confirmed-deleted (`stop`→`destroying`→
  absent proof, not acceptance).
- **Shared-Volume concurrency is deliberately narrow, from live evidence.**
  `fcntl.flock` + inode revalidation inside one mounted agent guards
  compare/mutate windows on one mount; live falsification proved independent
  sandbox mounts do NOT coordinate records through flock, so Memory mutation
  ownership is process-level: the leased Session Sandbox is the write path,
  explicit operator operations are immediate, autonomous promotion is
  single-lane (deterministic intents + one reconciler claim fence), and
  concurrent cross-Sandbox appends may lose records — never silently inside
  one owner.
- **Ownership seams are one-directional.** Provider objects die under
  `SandboxLease` receipts with typed cleanup; claimed Runs are orchestrated
  from claim through cleanup by `TurnRuntime`, with durable transitions
  owned by `RunLifecycleService`; Workspace operations ride the versioned
  installed agent whose handshake fails closed before use; Memory promotion
  rides the transactional outbox with bounded idempotent reconciliation.
- **Recursive child cleanup has one explicit lease state.** Acquisition,
  synchronous interpreter shutdown, provider cleanup/quarantine, and late
  acquisition adoption are separate Daytona seams. A child lease transitions
  `OPEN` → `CLOSING` → `CLOSED` only after cleanup succeeds; concurrent closes
  join the same owned cleanup, while a failed close remains `FAILED` and
  re-observes its recorded error instead of starting duplicate provider work.
- **Owned effects have one provider-neutral wait vocabulary.**
  `runtime.OwnedEffect` wraps caller-started asynchronous work, shields the
  effect from waiter cancellation, preserves terminal results/errors, and can
  return a bounded pending wait without cancelling the effect. It does not
  replace Run Ownership, child-runtime state, or Daytona lease/quarantine
  receipts; those domains retain their specialized state and fallback policy.

## Final maintainability freeze (P34)

- Every deep seam has one owner and one canonical representation. A private
  helper is acceptable when it keeps a boundary local; duplicate adapters,
  pass-through wrappers, repeated normalization/serialization, and dead aliases
  are not.
- `workspace/memory.py` classifies optional read-side degradation into the
  closed `MemoryFailureCategory` vocabulary. Diagnostics are bounded and
  sanitized; mutation and list paths remain strict. A new catch site must use
  the existing classifier rather than inventing a parallel warning format.
- `config/settings.py` is the source of truth for `FleetFieldPolicy` metadata
  and `config/policy.py` derives its editor inventory from it. No second
  complete configuration-field mirror is allowed.
- `tools/fleet-tui/src/tui/tests/turn-reducer-invariants.test.ts` is the
  deterministic P32 proof that live and durable projections converge through
  one canonical reducer. Wire adapters own casing and framing compatibility;
  the reducer does not.
- P34 excludes model routing/tuning, recursion or throughput changes, schema
  changes, Memory format changes, and public contract changes. A cleanup is
  valid only after all consumers and the full deterministic matrix have been
  checked. The live Daytona gate remains separately required evidence when the
  delivery claims live certification.

## Product identity

Fleet is one product: a durable conversational RLM Session. Judge every change
by whether it makes one of the following substantially better; if it does not,
question it:

- the conversational RLM agent — one compatible `dspy.RLM` per active Session
  runtime, reused across sequential successful Turns with a fresh per-Turn
  REPLHistory; bounded child RLM siblings provide controlled width
  at a fixed native depth
- Session continuity — a Session is a context boundary: new Sessions start
  cold, continuity exists only inside one Session; cross-session persistence
  happens through workspace-scope state (Attachments, Artifacts, and Workspace
  Memory); Memory writes are explicit, while each Turn receives only its bounded
  newest-record digest
- the Daytona workspace — durable shared Volume state with replaceable Sandbox
  compute attached to it
- the single client protocol — AI SDK UI v1 over SSE plus the generated
  artifacts owned by `make api-sync`

Fleet is not a general DSPy execution platform. Skills steer the agent's
guidance only; they never register host executables and never gate which tools
exist.

## Backend layers

- `api/` owns HTTP identity, dependency aliases, schemas, routes, OpenAPI, and
  SSE projection. Routes do not construct runtime stores, engines, LMs, or
  provider clients.
- `app.create_app()` installs the FastAPI shell and static bundled Skill
  catalog/authorizer/empty capability registry. Lifespan owns a validated
  `RuntimeInventory`, publishes readiness last, and detaches it before disposal.
- `composition/` owns complete common, Daytona, and private testing wiring.
- `chat/` owns preparation, Turn runtime, Turn Lifecycle, terminal ordering, and
  cleanup. `TurnRuntime` owns the claim-to-cleanup Run state machine and
  public stream facade. `RunLifecycle.finish()` owns Artifact publication and
  atomic commit.
- Turn Claim persistence has one typed `transition_claim()` operation. Its pure
  command/state policy is shared by in-memory and SQL adapters; successful
  commit and cancellation requests remain separate.
- `rlm/` owns model roles, Signature inputs, Session-scoped native RLM
  construction/reuse, fresh per-Turn REPLHistory,
  options, Runtime Events, delegation metrics, routing evaluation, cancellation,
  and fixed-depth child execution. `RLMRunner.stream(context)` remains the deep
  execution seam; `rlm/runtime.py` owns the cancellation-shielded
  worker/thread/event-loop boundary and the bounded detail
  relay/monitoring/drain policy, and `rlm/events.py` owns trace and
  recursive-metric projection. These remain private implementation seams.
- `daytona/` is the exclusive SDK boundary and owns provider-error normalization.
  `DaytonaRuntimeResources` owns provider resources and exposes the public
  `DaytonaRuntime` root/child lifecycle; composition injects database,
  binding, model, preparation, limits, and cleanup ports.
- `sessions/`, `workspace/`, `attachments/`, `artifacts/`, and `skills/` own
  domain interfaces and deterministic policy. `persistence/` owns SQLAlchemy
  adapters.
- Imports remain credential-free and side-effect-free.

- `daytona/broker.py` is the sole owner of broker source generation,
  HTTP-in-sandbox transport, host-tool/SUBMIT lifecycle, and the injected
  synchronous DSPy bridge.
  Source generation does not own provider lifecycle or persistence.

## Turn and async boundary

- Reuse one compatible `dspy.RLM` and custom interpreter for a resident Session
  runtime across sequential successful Turns. Bind a fresh host-tool set and
  REPLHistory per Turn; rotate the resident runtime after taint,
  incompatibility, or failed cleanup. Interpreters are never shared across
  Runs concurrently.
- Pass request text, complete committed `dspy.History`, bounded
  `session_context`, authorized `skill_cards`, and bounded Attachment metadata
  to the default Fleet Signature. Custom Task Contracts receive only declared
  host-bounded inputs. Keep `read_session_history` as an explicit compatibility
  Tool and call `await rlm.acall(interpreter, **named_inputs)`;
  the interpreter is caller-owned and its lease remains with Fleet.
- The Turn stream opens immediately with transient `data-status` preparation
  heartbeats (never recorded); claim, preparation, Attachment-ownership, and
  Skill-selection failures then resolve as closed `error` + `finish` chunks
  inside the stream instead of HTTP error statuses, while request-schema
  validation and composition readiness still answer 422/503 pre-headers.
- Artifact Candidates remain private until byte promotion and atomic Turn Commit
  succeed.
- Success ordering is `artifact.created*` then exactly one `run.completed`.
  Failure produces exactly one sanitized terminal and no history advance;
  cancellation produces one `abort` terminal (no `finish`/usage/checkpoint) and
  its settled attempt persists a bounded `data-status {phase="cancelled"}`
  tombstone pair in committed history.
- Hold an Interpreter Lease through finalization; release it during coordinator
  cleanup even after cancellation or repeated caller cancellation.
- Root-only `rlm_query_batched` reserves all child call indexes and prompt bytes
  atomically, preserves input order, and settles all-or-nothing. Its bounded
  sibling workers each own a copied DSPy Root/Sub runtime, tracing context, fresh
  Daytona Sandbox, Interpreter Lease, and cleanup. A child may use native
  `llm_query`/`llm_query_batched`, but cannot create another recursive batch.
- One absolute Turn deadline governs child admission, provider calls, batch join,
  and useful work. On expiry, authority is revoked and queued work is cancelled;
  running workers retain ownership until their deadline-bound cleanup settles.
  No executor shutdown, future, promotion task, or child lease may outlive the
  owner responsible for settling it.

- Startup recovery claims eligible nonterminal rows by owner before awaiting a
  provider fence. Daytona requires a bounded fence; deterministic compositions
  pass an explicit no-provider policy. A failed fence restores the prior owner,
  preserves the claim heartbeat, and records retry metadata.

Raw provider calls and exceptions never cross the `daytona/` boundary into
routes or public events.

## Skills and host tools

- Bundled Skills are versioned instruction/resource packages with progressive
  loading. Unrestricted Turns discover the full catalog; exact pinned Turns
  discover only their authorized selected set. They never register host
  executables.
- Runtime composition owns the explicit `dspy.Tool` objects for each Turn.
- HTTP may select up to four exact Skills but may not provide Python, callables,
  serialized Tools, or Signatures.
- Tool event views are host-owned bounded allowlists. No declared view means no
  public arguments or results.
- Daytona alone registers the Workspace Memory Tools: `read_workspace_memory`,
  `remember`, `list_memories`, `search_memories`, `edit_memory`, `forget` (plus the back-compat
  alias `update_workspace_memory`). Relevant plus newest complete records from
  `MEMORIES.md` are also injected into the Turn's `session_context` as a
  `workspace_memory tail` (bounded, tolerant, missing-store-safe); full history
  and search stay behind the read/list/search Tools. The append Tool permits a
  write only for an
  explicit user request. This is an auditable Tool-use policy, not a filesystem
  ACL, because the Daytona interpreter sees the mounted Volume.
- Workspace Memory uses generic Tool events only. Their allowlisted metadata
  never exposes the learning body, provider path, or raw error (ids, categories,
  byte counts only), and there is no dedicated memory Runtime Event.

## Persistence and storage

- Alembic owns live schema evolution; live PostgreSQL startup never calls
  `create_all`. Explicit SQLite test/local helpers may call `create_tables`.
- Durable Attachment and Artifact bytes live in Workspace Volume Scope.
- The mounted Workspace Agent is installed once per Sandbox as the complete,
  versioned `workspace_agent/runtime.py` artifact. Its explicit
  `handle(request)` validates and dispatches the same operation contract for
  installed and process-only fallback launchers; operations then run as
  compact handshake-bound JSON requests. Protocol, artifact digest, or
  capability mismatch fails closed before Workspace/Memory use. The host does
  not search, split, indent, or otherwise rewrite the remote source.
- Daytona Session Workspace text lives under
  `sessions/{session_id}/workspace/`. Paged reads, bounded immediate-child
  listings, append, replacement writes, unique-fragment edits, and file or
  empty-directory deletes are immediate private state, not Turn-commit
  candidates. Writes, appends, edits, and deletes accept optional SHA-256
  preconditions. Write/append compare-and-mutate runs inside one mounted
  Workspace agent operation with target locks and inode revalidation across
  I/O Sandboxes, without a distributed lock service or a separate host read.
  All mutations never recurse or follow symlinks, and direct Workspace Artifact
  publication only stages a private candidate.
- Daytona Workspace Memory is distinct workspace-wide immediate state. Its only
  target is `memory/MEMORIES.md` under the already mounted
  `workspaces/<workspace_id>` Volume subpath (legacy root `MEMORIES.md` is
  migrated on first open, never losing content); Session and Run paths remain
  nested below that root.
- Memory updates are id-addressed complete UTC-timestamped records of at most
  4 KiB formatted UTF-8: explicit-user appends write v3 records
  (`- [ts] **Category** <!-- id:8hex -->: learning`) with fresh ids and are
  idempotent for the same normalized record. v1 rows derive a deterministic id
  from their canonical text plus valid-record occurrence, so duplicate legacy
  rows remain separately pageable; duplicate persisted ids fail closed. `edit_memory`
  upgrades legacy rows to v3 or replaces one v3 line while preserving id and timestamp,
  and `forget` removes exactly one entry. Both mutations perform their
  read-modify-fsync-publish rewrite in one mounted-agent operation. Records
  become durable independently of Turn Commit and survive failed or cancelled
  Turns and Sandbox replacement. Reads are tolerant: humans edit this file, so
  malformed lines are skipped with a bounded warning count; strict validation
  still governs what Tools write. Reads return the newest complete records
  within the fixed 256 KiB read budget.
  `max_upload_bytes` caps the complete file; appends against full or torn state
  and access to unsafe or invalid storage fail closed, with no automatic
  compaction, deletion, or repair.
- Autonomous Memory candidates, when enabled by policy, are promoted only after
  successful Turn Commit. The promotion task remains owned until it settles
  before the Run's lease and mounted resources release; its post-commit wait is
  bounded and does not detach work from those resources.
- Memory append serialization is process-local. Separate Fleet processes are
  not coordinated, so concurrent cross-process append is not guaranteed. The
  live cross-Sandbox, cross-Session proof remains gated and has not been run.
- Daytona acquisition idempotently creates canonical shared, Session, and Run
  directories and fails closed on mount/provider conflicts.
- Workspace events expose relative paths, counts, sizes, and status, never file
  contents, provider paths, or raw failures.
- Bytes precede metadata. Orphan bytes may be GC-eligible; uncommitted rows or
  public success claims are forbidden.

## Configuration and compatibility

- Runtime settings resolve only environment names explicitly referenced by the
  selected TOML policy; ambient selectors and unreferenced variables are
  ignored. The canonical public Run Environment is `daytona`.
- `RLM_NATIVE_CHILD_DEPTH = 1` is a product invariant. The policy
  `recursion_max_parallel_children` bounds Root-selected sibling workers; it
  does not expose concurrency or depth controls to the model and does not permit
  child-controlled fan-out. A child may use native semantic tools and its
  recursive request becomes a Sub-LM depth fallback rather than a grandchild.
- There is no `/api/v1`, WebSocket execution, dual serve, legacy migration,
  runtime-admin, optimization/evaluation API, or public Artifact creation.
- The maintained client is pi-tui. A graphical/Web client is separate future
  work and must use the public HTTP/SSE contract.

## Generated artifacts

Do not hand-edit either generated contract:

```bash
make api-sync   # openapi.yaml + tools/fleet-tui/src/generated/openapi.ts
make api-check
```

## Script boundary

Every top-level Python helper under `scripts/` is listed in `scripts/README.md`
and supports `uv run python scripts/<name>.py --help` where applicable.

## Remediation

1. Move code back to its owning module.
2. Prefer an existing domain interface over a new cross-layer import.
3. If an invariant is obsolete, update this file, root `AGENTS.md`, and its
   automated check together.
4. Run `make check-docs` before finishing.
