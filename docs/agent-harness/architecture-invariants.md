# Architecture Invariants

If a change violates an invariant, remediate the code or update this document
and its matching automated check in the same patch.

## Product identity

Fleet is one product: a durable conversational RLM Session. Judge every change
by whether it makes one of the following substantially better; if it does not,
question it:

- the conversational RLM agent — one fresh `dspy.RLM` per Turn; DSPy primitives
  support that agent, they are not peer execution modes
- Session continuity — a Session is a context boundary: new Sessions start
  cold, continuity exists only inside one Session; cross-session persistence
  happens only through explicit workspace-scope actions (Attachments,
  Artifacts, Workspace Memory), never automatically
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
- `chat/` owns preparation, coordination, Turn Lifecycle, terminal ordering, and
  cleanup. `chat/turn_execution.py` owns the private post-preparation state
  machine; `TurnLifecycle.finish()` owns Artifact publication and atomic commit;
  `TurnCoordinator` owns the public stream facade and resource release.
- Turn Claim persistence has one typed `transition_claim()` operation. Its pure
  command/state policy is shared by in-memory and SQL adapters; successful
  commit and cancellation requests remain separate.
- `rlm/` owns model roles, Signature inputs, fresh native RLM construction,
  options, Runtime Events, cancellation, and execution.
- `daytona/` is the exclusive SDK boundary and owns provider-error normalization.
  `DaytonaRuntimeResources` remains provider-only; composition injects database,
  binding, model, preparation, limits, and cleanup ports.
- `sessions/`, `files/`, `artifacts/`, and `skills/` own domain interfaces and
  deterministic policy. `persistence/` owns SQLAlchemy adapters.
- Imports remain credential-free and side-effect-free.

- `daytona/broker_source.py` contains pure broker source generation. The HTTP
  broker may re-export those helpers for compatibility, but source generation
  does not own provider lifecycle or persistence.

## Turn and async boundary

- Construct a fresh `dspy.RLM` and host-tool list per Turn. Daytona constructs a
  fresh custom interpreter. Interpreters are never shared across Runs or
  concurrently.
- Pass request text, bounded `session_context`, authorized `skill_cards`, and
  bounded Attachment metadata to the default Fleet Signature. Custom Task
  Contracts receive only declared host-bounded inputs. Keep older history behind
  `read_session_history` and call `await rlm.acall(**named_inputs)`.
- Attachment ownership and Skill selection validation finish before SSE begins.
- Artifact Candidates remain private until byte promotion and atomic Turn Commit
  succeed.
- Success ordering is `artifact.created*` then exactly one `run.completed`.
  Failure produces exactly one sanitized terminal and no history advance.
- Hold an Interpreter Lease through finalization; release it during coordinator
  cleanup even after cancellation or repeated caller cancellation.

- Startup recovery claims eligible nonterminal rows by owner before awaiting a
  provider fence. Daytona requires a bounded fence; deterministic compositions
  pass an explicit no-provider policy. A failed fence restores the prior owner,
  preserves the claim heartbeat, and records retry metadata.

Raw provider calls and exceptions never cross the `daytona/` boundary into
routes or public events.

## Skills and host tools

- Bundled Skills are versioned instruction/resource packages with progressive
  loading. They never register host executables.
- Runtime composition owns the explicit `dspy.Tool` objects for each Turn.
- HTTP may select up to four exact Skills but may not provide Python, callables,
  serialized Tools, or Signatures.
- Tool event views are host-owned bounded allowlists. No declared view means no
  public arguments or results.
- Daytona alone registers `read_workspace_memory` and
  `update_workspace_memory`. Memory is loaded only when the RLM calls the read
  Tool; it is never injected at Turn start. The update Tool permits an append
  only for an explicit user request. This is an auditable Tool-use policy, not a
  filesystem ACL, because the Daytona interpreter sees the mounted Volume.
- Workspace Memory uses generic Tool events only. Their allowlisted metadata
  never exposes the learning body, provider path, or raw error, and there is no
  dedicated memory Runtime Event.

## Persistence and storage

- Alembic owns live schema evolution; live PostgreSQL startup never calls
  `create_all`. Explicit SQLite test/local helpers may call `create_tables`.
- Durable Attachment and Artifact bytes live in Workspace Volume Scope.
- Daytona Session Workspace text lives under
  `sessions/{session_id}/workspace/`. Paged reads, bounded immediate-child
  listings, append, and replacement writes are immediate private state, not
  Turn-commit candidates; direct Workspace Artifact publication only stages a
  private candidate.
- Daytona Workspace Memory is distinct workspace-wide immediate state. Its only
  target is the root `MEMORIES.md` of the already mounted
  `workspaces/<workspace_id>` Volume subpath; Session and Run paths remain
  nested below that root.
- Memory updates are append-only, complete UTC-timestamped records of at most
  4 KiB formatted UTF-8. They become durable independently of Turn Commit and
  survive failed or cancelled Turns and Sandbox replacement. Reads return the
  newest complete records within the fixed 256 KiB read budget.
  `max_upload_bytes` caps the complete file; appends against full or torn state
  and access to unsafe or invalid storage fail closed, with no automatic
  compaction, deletion, or repair.
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

- Runtime settings use only `FLEET_*`; the canonical public environment is
  `daytona`.
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
