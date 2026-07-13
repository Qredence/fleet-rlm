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
  Runtime Events, budgets, cancellation, and execution.
- `daytona/` is the exclusive Daytona SDK boundary and owns Sandbox, lease,
  Volume, interpreter, and provider-error normalization.
- `sessions/`, `files/`, `artifacts/`, and `skills/` own domain interfaces and
  deterministic policy. `persistence/` owns SQLAlchemy adapters.
- Application-lifetime resources are created and disposed by FastAPI lifespan.
  Package imports remain credential-free and side-effect-free.

## Turn and async boundary

- Construct a fresh `dspy.RLM`, custom `CodeInterpreter`, and host-tool list per
  Turn; custom interpreters are not shared concurrently.
- Preserve sandbox-safe `history: list[dict]` and invoke
  `await rlm.aforward(**named_signature_inputs)`.
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
- Bytes are written before metadata. UUID-unique orphan bytes are acceptable;
  rows or public success claims without committed metadata are not.

## Configuration and compatibility

- Runtime settings use only the `FLEET_*` surface. Do not add environment,
  import, schema, route, or command aliases for the deleted backend.
- There is no `/api/v1`, WebSocket execution, dual-serve, legacy data migration,
  runtime-admin, optimization/evaluation API, or public Artifact creation.
- `src/frontend/` is a separate consumer until its SSE adaptation; backend work
  does not synchronize or package frontend artifacts.

## Frontend boundaries

Existing frontend source remains governed by its own `AGENTS.md`. Backend-only
work must not edit frontend source, generated OpenAPI copies, route trees, or
build artifacts.

## Generated artifacts

Do not hand-edit root `openapi.yaml`. Use:

```bash
make api-sync
make api-check
```

These commands are backend-only. Frontend generated contracts are intentionally
not synchronized by the backend gate.

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
