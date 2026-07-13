# Persistence DB package dossier

Structural cleanup of the SQLAlchemy catalog and Postgres repository package.
This work is **refactor-only** and **must not gate** [Phase 9 direct RLM
promotion](../09-direct-rlm-promotion/README.md).

## Sequencing relative to the product path

```text
Phase 8 GEPA quality (product)          # may still be partial (validation/smoke)
  → Phase 9 direct-RLM promotion        # product
  → Phase 10 frontend SSE cleanup       # product

Phase 8.5A/B persistence package        # structural, non-blocking for Phase 9
```

Rules:

1. Phase 9 is never blocked on 8.5 completion.
2. 8.5A/B preserve behavior: `alembic check` green, no intentional migrations,
   and an unchanged public `PersistenceProtocol` / `FleetRepository` method
   surface.
3. Postgres remains the system of record. LocalStore stays a limited dual
   backend and is not expanded to full Neon parity in this phase.
4. Legacy flat package `integrations/database/` is **removed** after first-party
   importers migrate to `fleet_rlm.db` (no permanent dual implementation).

## Phase 8.5A — Model registry and db package move

- **Order:** `8.5`
- **Status:** `complete`
- **Track:** `Persistence`
- **Summary:** Move the SQLAlchemy catalog and domain repositories into `src/fleet_rlm/db/` with one Alembic model registry; migrate importers; delete legacy flat package.
- **Owner:** `src/fleet_rlm/db/`

### Goal and stable interfaces

Canonical ownership:

```text
src/fleet_rlm/db/
  __init__.py
  base.py
  enums.py
  engine.py
  models/
    __init__.py           # Alembic registry
    identity.py
    chat_runtime.py
    optimization.py
    memory.py
    llm_profiles.py
    ops.py                # jobs + sandbox (8.5B)
  repos/
    shared.py
    identity.py
    chat.py
    optimization.py
    memory.py
    jobs.py
    fleet.py
```

- `migrations/env.py` imports models only through `fleet_rlm.db.models`.
- `PersistenceProtocol` and LocalStore remain under `integrations/`.
- Flat `src/fleet_rlm/integrations/database/` is **gone**.

### Non-goals

- Schema redesign, RLS rewrite, or Postgres enum encoding changes.
- LocalStore full Neon parity or LocalStore rewrite.
- Merging domain repositories into one file.
- Introducing a full DTO layer at every port boundary.
- Flipping `execution_backend` or deleting WebSocket paths (Phases 9–10).
- Gating Phase 9 promotion on this package move.

### Acceptance criteria

- [x] All ORM models importable from one registry; `migrations/env.py` imports only that registry.
- [x] Canonical package is `src/fleet_rlm/db/` with domain `models/` and `repos/`.
- [x] First-party importers use `fleet_rlm.db.*`; legacy `integrations/database` package removed.
- [x] No intentional schema delta (+0 migrations for this refactor).
- [x] `PersistenceProtocol` and `FleetRepository` public method surface unchanged.
- [x] Focused DB and chat-persistence tests pass.
- [x] [Target architecture](../../target-architecture.md) ownership lists live `db/`.

### Validation

```bash
uv run alembic check
uv run pytest tests/unit/integrations/test_db_engine.py \
  tests/unit/api/test_chat_persistence.py -q
test ! -d src/fleet_rlm/integrations/database
rg "fleet_rlm\.integrations\.database" src tests scripts migrations && exit 1 || true
make typecheck
make check-docs
uv run python scripts/sync_plans_canvas.py --check
```

### Rollback and compatibility

- Revert the package-move commit(s) if needed.
- No migrate-down is required when the refactor is metadata-identical.

### Evidence

See [evidence-package-move.md](evidence-package-move.md).

## Phase 8.5B — Ops model domain merge

- **Order:** `8.51`
- **Status:** `complete`
- **Track:** `Persistence`
- **Summary:** Merge jobs and sandbox table modules into `models/ops.py` without schema change.
- **Owner:** `src/fleet_rlm/db/models/`

### Acceptance criteria

- [x] Tiny domains merged into `models/ops.py` without metadata change.
- [x] Model registry and imports updated; focused tests green.
- [x] Still +0 intentional Alembic migrations for this refactor.

## Deferred gaps

- Shrink LocalStore to an explicit capability matrix (post–Phase 9 opportunistic).
- Thin fat repositories when a product change already touches that domain.
- Move `PersistenceProtocol` to `db/port.py` without expanding LocalStore.
- Optional DTO boundary on hot API surfaces only where ORM leakage hurts.

Phase codes use uppercase letter suffixes (`8.5A`, `8.5B`) so the plans-canvas
parser matches the same pattern as `2A` / `3F`.
