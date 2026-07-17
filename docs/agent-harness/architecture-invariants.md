# Architecture Invariants

If a change violates an invariant, remediate the code or update this document
and its matching automated check in the same patch.

## Backend layers

- `api/` owns HTTP identity, dependency aliases, schemas, routes, and SSE
  projection. Routes retrieve lifespan-composed modules and do not construct
  stores, repositories, engines, or provider clients.
- `chat/` owns Turn context construction, orchestration, Turn Commit, terminal
  ordering, and final Interpreter Lease release.
- `rlm/` owns DSPy model roles, signature inputs, fresh-per-Turn RLM creation,
  RLM Options, Runtime Events, cancellation, and execution.
- `daytona/` is the exclusive Daytona SDK boundary and owns Sandbox, lease,
  Volume, interpreter, and provider-error normalization.
- `sessions/`, `files/`, `artifacts/`, and `skills/` own domain interfaces and
  deterministic policy. `persistence/` owns SQLAlchemy adapters.
- Application-lifetime resources are created and disposed by FastAPI lifespan.
  Package imports remain credential-free and side-effect-free.

## Turn and async boundary

- Construct a fresh `dspy.RLM` and host-tool list per Turn. Daytona constructs
  a fresh custom `CodeInterpreter`; Deno passes `interpreter=None` so DSPy owns
  a fresh default Deno/Pyodide interpreter. Interpreters are never shared
  concurrently.
- Pass a bounded sandbox-safe `session_context: dict` to the default Fleet
  Signature and only declared host-bounded inputs to custom Task Contracts.
  Keep older committed messages behind the Session-scoped
  `read_session_history` Tool. Include only bounded runtime availability—not a
  file listing—for the Session Workspace, and invoke the supported
  `await rlm.acall(**named_signature_inputs)` surface.
- Attachment ownership validation finishes before SSE begins.
- Artifact Candidates remain private until byte promotion and transactional Turn
  Commit succeed.
- Success ordering is `artifact.created*` then exactly one `run.completed`.
  Failure produces exactly one sanitized error terminal and no history advance.
- `TurnCoordinator` holds the Interpreter Lease through persistence and terminal
  projection and releases it in `finally`.

The custom Daytona interpreter bridges blocking provider calls without exposing
them to FastAPI routes. Do not move provider calls or raw exceptions across the
`daytona/` boundary.

## Persistence and storage

- Alembic owns live schema evolution; live startup never calls `create_all`.
  Explicit SQLite test/offline helpers may call `create_tables`.
- Durable Attachment and Artifact bytes live in Workspace Volume Scope.
  Non-Turn I/O uses short-lived, workspace-labelled Sandboxes that mount only
  `workspaces/<workspace_id>` and are explicitly deleted in `finally`.
- Daytona Session Workspace text lives under
  `sessions/{session_id}/workspace/` inside Workspace Volume Scope. Successful
  writes are immediate private working state, not Turn-commit candidates; Deno
  registers no workspace tools. Before returning an Interpreter Lease, Daytona
  acquisition idempotently creates the canonical shared roots and current
  Session/Run container directories.
- Workspace tool events expose relative paths, counts, sizes, and status but
  never file contents, provider paths, or raw provider failures.
- Bytes are written before metadata. UUID-unique orphan bytes are acceptable;
  rows or public success claims without committed metadata are not.

## Configuration and compatibility

- Runtime settings use only the `FLEET_*` surface. Do not add environment,
  import, schema, route, or command aliases for the deleted backend.
- There is no `/api/v1`, WebSocket execution, dual-serve, legacy data migration,
  runtime-admin, optimization/evaluation API, or public Artifact creation.
- No frontend source tree or generated frontend contract is currently retained.
  A future client must integrate through the public AI SDK UI 7 SSE contract.

## Generated artifacts

Do not hand-edit root `openapi.yaml`. Use:

```bash
make api-sync
make api-check
```

These commands own the backend contract.

## Script boundary

Every top-level Python helper under `scripts/` is listed in `scripts/README.md`
and supports `uv run python scripts/<name>.py --help` where applicable.

## Remediation

When a boundary check fails:

1. Move code back to its owning module.
2. Prefer an existing domain interface over a new cross-layer import.
3. If the invariant is obsolete, update this file, root `AGENTS.md`, and the
   matching harness check together.
4. Run `make check-docs` before finishing.
