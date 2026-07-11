# Persistence DB package dossier

Structural cleanup of the SQLAlchemy catalog and Postgres repository package.
This work is **refactor-only** and **must not gate** [Phase 9 direct RLM
promotion](../09-direct-rlm-promotion/README.md).

## Sequencing relative to the product path

```text
Phase 8 GEPA quality (product)          # current critical path
  → Phase 9 direct-RLM promotion        # product
  → Phase 10 frontend SSE cleanup       # product

Phase 8.5A/B persistence package        # structural, non-blocking for Phase 9
  parallel after Phase 8 schema freeze, or between 8 and 9 only if cheap
```

Rules:

1. Phase 9 is never blocked on 8.5 completion.
2. Prefer running 8.5A when Phase 8 optimization schema is frozen enough that
   concurrent GEPA migrations will not thrash renames; pure registry work may
   start earlier if it does not move modules yet.
3. 8.5A/B preserve behavior: `alembic check` green, no intentional migrations,
   and an unchanged public `PersistenceProtocol` / `FleetRepository` method
   surface.
4. Postgres remains the system of record. LocalStore stays a limited dual
   backend and is not expanded to full Neon parity in this phase.

Live code today remains under `src/fleet_rlm/integrations/database/`. The
`src/fleet_rlm/db/` package is the **target** ownership home until 8.5A lands.

## Phase 8.5A — Model registry and db package move

- **Order:** `8.5`
- **Status:** `planned`
- **Track:** `Persistence`
- **Summary:** Move the SQLAlchemy catalog and domain repositories into `src/fleet_rlm/db/` with one Alembic model registry and compatibility re-exports.
- **Owner:** `src/fleet_rlm/db/`
- **Effort:** about 3–5 days when schema is stable

### Prerequisites

- Phase 8 product work is not mid-flight on conflicting optimization schema
  renames, or 8.5A is limited to registry-only steps that do not move modules.
- [Target architecture](../../target-architecture.md) ownership for `db/` is
  documented (roadmap PR).

### Goal and stable interfaces

Canonical ownership:

```text
src/fleet_rlm/db/
  __init__.py
  base.py                 # DeclarativeBase, _pg_enum
  enums.py
  engine.py
  models/
    __init__.py           # Alembic registry — import every table module
    identity.py
    chat_runtime.py       # sessions, turns, runs, steps, events, artifacts, traces
    optimization.py
    memory.py
    llm_profiles.py
    jobs.py               # until 8.5B
    sandbox.py            # until 8.5B
  repos/
    shared.py             # RLS session scope, request context
    identity.py
    chat.py
    optimization.py
    memory.py
    jobs.py
    fleet.py              # FleetRepository façade
```

- Models define schema only (tables, constraints, indexes, enums).
- Repositories own use-case transactions, RLS `set_config`, and queries.
- `FleetRepository` (or equivalent Postgres store) remains the DI façade
  implementing `PersistenceProtocol`.
- `migrations/env.py` imports models only through `fleet_rlm.db.models`.
- `fleet_rlm.integrations.database` re-exports keep existing importers working
  for one release window.

Related live modules until code moves: `src/fleet_rlm/integrations/database/`,
`src/fleet_rlm/integrations/persistence_protocol.py`,
`src/fleet_rlm/integrations/local_store.py`, `migrations/env.py`.

### Non-goals

- Schema redesign, RLS rewrite, or Postgres enum encoding changes.
- Omnigent-style single megafile or int enum codecs.
- LocalStore full Neon parity or LocalStore rewrite.
- Merging domain repositories into one file.
- Introducing a full DTO layer at every port boundary.
- Flipping `execution_backend` or deleting WebSocket paths (Phases 9–10).
- Gating Phase 9 promotion on this package move.

### Acceptance criteria

- [ ] All ORM models importable from one registry; `migrations/env.py` imports only that registry.
- [ ] Canonical package is `src/fleet_rlm/db/` with domain `models/` and `repos/`.
- [ ] Compat re-exports from `fleet_rlm.integrations.database` keep existing importers working.
- [ ] No intentional schema delta (`alembic check` green; +0 migrations).
- [ ] `PersistenceProtocol` and `FleetRepository` public method surface unchanged.
- [ ] Focused DB and chat-persistence tests pass; typecheck and docs gates pass.
- [ ] [Target architecture](../../target-architecture.md) ownership lists `db/`.

### Validation

```bash
uv run alembic check
uv run pytest tests/unit/integrations/test_db_engine.py \
  tests/integration/database/ tests/unit/api/test_chat_persistence.py -q
make typecheck
make check-docs
uv run python scripts/sync_plans_canvas.py --check
```

### Rollback and compatibility

- Revert the package move; restore `integrations/database` as the implementation
  home if needed.
- Compat re-exports mean callers need not change on day one.
- No migrate-down is required when the refactor is metadata-identical.

### Evidence

Record implementation evidence beside this dossier as `evidence-*.md` when 8.5A
code lands. Until then acceptance items stay unchecked.

## Phase 8.5B — Optional tiny model domain merge

- **Order:** `8.51`
- **Status:** `planned`
- **Track:** `Persistence`
- **Summary:** Merge the smallest table domains into `models/ops.py` without schema change.
- **Owner:** `src/fleet_rlm/db/models/`
- **Effort:** about 1 day after 8.5A

### Prerequisites

- Phase 8.5A complete (canonical `db/` package and registry exist).

### Goal and stable interfaces

- Merge `jobs` and `sandbox_sessions` table modules into `models/ops.py`.
- Keep identity, chat_runtime, optimization, memory, and llm_profiles as
  separate domain modules (change cadence and risk differ).
- Registry and re-exports updated; still no schema delta.

### Non-goals

- Merging optimization or chat_runtime into ops.
- Repository file merges.
- Any column, index, RLS, or enum change.

### Acceptance criteria

- [ ] Tiny domains merged into `models/ops.py` without metadata change.
- [ ] Model registry and imports updated; focused tests green.
- [ ] Still +0 intentional Alembic migrations; `alembic check` green.

### Validation

```bash
uv run alembic check
uv run pytest tests/unit/integrations/test_db_engine.py \
  tests/integration/database/ -q
make typecheck
make check-docs
```

### Rollback and compatibility

- Restore separate `jobs.py` / `sandbox.py` modules if review or merge conflict
  cost is too high; ops merge is optional hygiene only.

## Deferred gaps

- Shrink LocalStore to an explicit capability matrix (post–Phase 9 opportunistic).
- Thin fat repositories when a product change already touches that domain.
- Move `PersistenceProtocol` to `db/port.py` without expanding LocalStore.
- Remove the `integrations/database` shim after import inventory proves no
  remaining consumers (Phase 10-adjacent cleanup with evidence).
- Optional DTO boundary on hot API surfaces only where ORM leakage hurts.

Phase codes use uppercase letter suffixes (`8.5A`, `8.5B`) so the plans-canvas
parser matches the same pattern as `2A` / `3F`.
