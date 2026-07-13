# Phase 8.5 evidence — db package move and legacy cleanup

## Summary

Behavior-preserving move of the Postgres SQLAlchemy catalog and domain
repositories from flat `src/fleet_rlm/integrations/database/` to
`src/fleet_rlm/db/` with domain `models/` + `repos/`, one Alembic registry, and
full first-party import migration. The legacy package directory was deleted.
Jobs + sandbox models merged into `models/ops.py` (8.5B) in the same change set.

## What landed

| Item | Path |
|------|------|
| Engine / base / enums | `src/fleet_rlm/db/engine.py`, `base.py`, `enums.py` |
| Model registry | `src/fleet_rlm/db/models/__init__.py` (31 tables on `Base.metadata`) |
| Domain models | `identity`, `chat_runtime`, `optimization`, `memory`, `llm_profiles`, `ops` |
| Domain repos | `repos/{shared,identity,chat,optimization,memory,jobs,fleet}.py` |
| Alembic entry | `migrations/env.py` → `from fleet_rlm.db.models import Base` |
| Dual backend | Unchanged location: `integrations/persistence_protocol.py`, `local_store.py` |
| Removed | Entire `src/fleet_rlm/integrations/database/` tree |

## Gates run (local)

```bash
uv run python -c "from fleet_rlm.db.models import Base; assert len(Base.metadata.tables)==31"
uv run pytest tests/unit/integrations/ \
  tests/unit/api/test_chat_persistence.py \
  tests/unit/api/test_chat_activation_wiring.py \
  tests/unit/quality/test_activation_lifecycle.py \
  tests/unit/quality/test_phase8_contracts.py -q
make format-check && make lint && make check-docs
uv run python scripts/sync_plans_canvas.py --check
test ! -d src/fleet_rlm/integrations/database
# No remaining Python imports of fleet_rlm.integrations.database under src/tests/scripts/migrations
```

## Live Postgres / Alembic

Attempted `alembic check` and `tests/integration/database/` against configured Neon
(`DATABASE_URL` / `DATABASE_ADMIN_URL` present). Connect failed from this
environment: TCP timeouts / no route to host to the Neon pooler. Not a schema
delta from this refactor—network path only. Re-run when Neon is reachable:

```bash
uv run alembic check
uv run pytest tests/integration/database/ -q
```

## Schema

- No new Alembic revision for this refactor.
- In-process metadata: 31 tables registered via `fleet_rlm.db.models`.

## Non-claims

- Does not complete Phase 8 GEPA live smoke or flip Phase 9 defaults.
- Does not expand LocalStore parity.
