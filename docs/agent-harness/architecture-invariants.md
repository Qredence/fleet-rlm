# Architecture Invariants

If a change violates an invariant, remediate the code or update this document
and its matching automated check in the same patch.

## Backend layers

- `api/` owns HTTP identity, dependency aliases, schemas, routes, OpenAPI, and
  SSE projection. Routes do not construct runtime stores, engines, LMs, or
  provider clients.
- `app.create_app()` installs the FastAPI shell and static bundled Skill
  catalog/authorizer/empty capability registry. Lifespan owns runtime inventory.
- `composition/` owns complete common, Deno, Daytona, and private testing wiring.
- `chat/` owns preparation, coordination, Turn Lifecycle, terminal ordering, and
  cleanup. `TurnLifecycle.finish()` owns Artifact publication and atomic commit;
  `TurnCoordinator` owns stream settlement and resource release.
- `rlm/` owns model roles, Signature inputs, fresh native RLM construction,
  options, Runtime Events, cancellation, and execution.
- `daytona/` is the exclusive SDK boundary and owns provider-error normalization.
- `sessions/`, `files/`, `artifacts/`, and `skills/` own domain interfaces and
  deterministic policy. `persistence/` owns SQLAlchemy adapters.
- Imports remain credential-free and side-effect-free.

## Turn and async boundary

- Construct a fresh `dspy.RLM` and host-tool list per Turn. Daytona constructs a
  fresh custom interpreter; Deno passes `interpreter=None`. Interpreters are
  never shared across Runs or concurrently.
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

## Persistence and storage

- Alembic owns live schema evolution; live PostgreSQL startup never calls
  `create_all`. Explicit SQLite test/local helpers may call `create_tables`.
- Durable Attachment and Artifact bytes live in Workspace Volume Scope.
- Daytona Session Workspace text lives under
  `sessions/{session_id}/workspace/`. Writes are immediate private state, not
  Turn-commit candidates; Deno registers no workspace tools.
- Daytona acquisition idempotently creates canonical shared, Session, and Run
  directories and fails closed on mount/provider conflicts.
- Workspace events expose relative paths, counts, sizes, and status, never file
  contents, provider paths, or raw failures.
- Bytes precede metadata. Orphan bytes may be GC-eligible; uncommitted rows or
  public success claims are forbidden.

## Configuration and compatibility

- Runtime settings use only `FLEET_*`; canonical public environments are `deno`
  and `daytona`.
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
