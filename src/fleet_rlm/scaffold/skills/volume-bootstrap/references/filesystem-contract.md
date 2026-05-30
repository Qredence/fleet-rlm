# Filesystem Contract

## What `ensure_daytona_volume_layout()` Creates

Called automatically during `acreate_workspace_session()` when `volume_name` is set.

Creates (idempotent — safe to call multiple times, never overwrites existing content):

```
/home/daytona/memory/
├── memory/           ← legacy key-value store
├── artifacts/        ← produced outputs
├── buffers/          ← named buffer lists
├── meta/             ← legacy workspace metadata
├── memories/         ← persistent memory DB location
├── knowledge/
│   ├── ingested/     ← raw documents
│   └── summaries/    ← generated summaries
├── skills/
│   ├── system/       ← auto-seeded bundled skills
│   └── user/         ← user-added skills
├── sessions/         ← session conversation manifests
├── logs/             ← runtime logs
└── uploads/          ← pre-ingestion staging
```

## What `seed_system_skills()` Populates

Copies all SKILL.md files from `fleet_rlm.scaffold.skills` package into
flat files at `skills/system/<skill_name>.md`. The packaged `references/` and
`scripts/` directories remain available in the installed Python package; the
current volume seeding step does not copy them into the mounted volume.

Idempotent: skips files that already exist. Never overwrites user modifications.

## What `init_memory_db()` Creates

Bootstraps `memories/core.db` with:
- `memory` table (see `references/memory-db-schema.md`)
- `schema_migrations` tracking table
- Indexes for scope and updated_at
- PRAGMA user_version = 2

If `core.db` already exists, applies only missing migrations (version check).

## Idempotency Guarantee

All three bootstrap functions are designed to be called on every session start:
- Directory creation uses `mkdir -p` semantics (no error if exists)
- Skill seeding checks file existence before writing
- DB init checks schema version before migrating

This means: volumes survive sandbox restarts, upgrades, and repeated sessions
without data loss or corruption.

## Legacy Root Compatibility

The legacy roots (`memory/`, `artifacts/`, `buffers/`, `meta/`) still exist
and are still created by the bootstrap. Code written against the old layout
continues to work. New code should prefer:

| Legacy | Preferred |
|--------|-----------|
| `memory/` (flat files) | `remember()`/`recall()` via `memories/core.db` |
| `artifacts/` | Still canonical for file-based outputs |
| `buffers/` | `add_buffer()`/`get_buffer()` (session-scoped) |
| `meta/workspaces/...` | `sessions/<id>/conversation.json` |

## Child Sandbox Volume Sharing

In `context` isolation mode, the child sandbox shares the parent's volume mount.
This means:
- Child can READ anything the parent wrote to `MEMORY_ROOT/`
- Child can WRITE to the volume (but `remember()` is depth-gated)
- File conflicts are possible if parent and child write to same path concurrently
- Use unique filenames or session-scoped paths to avoid conflicts

In `clean` isolation mode, the child gets NO volume access unless explicitly configured.
