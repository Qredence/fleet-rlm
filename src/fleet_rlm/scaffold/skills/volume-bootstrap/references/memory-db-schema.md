# Memory DB Schema — `memories/core.db`

## Overview

SQLite database at `MEMORY_ROOT/memories/core.db`. Auto-created by
`init_memory_db()` on first session with a volume attached.

## Schema Version

Current: **2** (tracked via `PRAGMA user_version`)

## Tables

### `memory`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `key` | TEXT | PRIMARY KEY | Unique memory identifier |
| `value` | TEXT | NOT NULL | Stored content (any serializable string) |
| `scope` | TEXT | DEFAULT 'core' | Namespace for memory grouping |
| `writer_agent_depth` | INTEGER | DEFAULT 0 | Depth of agent that wrote this entry |
| `created_at` | TEXT | DEFAULT CURRENT_TIMESTAMP | ISO creation time |
| `updated_at` | TEXT | DEFAULT CURRENT_TIMESTAMP | ISO last-update time |

### `schema_migrations`

| Column | Type | Description |
|--------|------|-------------|
| `version` | INTEGER | Migration version number |
| `description` | TEXT | Human-readable migration name |
| `applied_at` | TEXT | When migration was applied |

## Indexes

- `idx_memory_scope` — on `memory(scope)`
- `idx_memory_updated_at` — on `memory(updated_at)`

## CRUD Operations

### Write — `remember(key, value)`

```sql
INSERT OR REPLACE INTO memory (key, value, scope, writer_agent_depth, updated_at)
VALUES (?, ?, 'core', 0, CURRENT_TIMESTAMP)
```

- **Depth-gated**: Only root agent (depth=0) can execute writes
- **UPSERT**: Last writer wins — existing key gets value overwritten
- **Scope**: Always `'core'` via the tool interface
- Returns: `{status: "stored", key, scope, writer_agent_depth}`

### Read — `recall(query)`

```sql
SELECT key, value, scope, created_at, updated_at
FROM memory
WHERE key LIKE ? OR value LIKE ?
ORDER BY updated_at DESC, created_at DESC
LIMIT 50
```

- **No depth restriction**: Any agent at any depth can read
- **Pattern**: `%query%` on both key and value columns
- **Limit**: 50 results maximum
- Returns: list of `{key, value, scope, created_at, updated_at}`

## Depth-Gating Logic

```python
if agent_depth > 0:
    return {"status": "rejected", "reason": "child agents are read-only"}
```

This prevents recursive child agents from polluting the memory store.
Only the root-level agent (depth=0) can create or modify entries.

## Migration System

Migrations are tracked in `schema_migrations`. On startup:
1. Check `PRAGMA user_version`
2. If < current version, apply missing migrations in order
3. Update `PRAGMA user_version` after all migrations succeed

Current migrations:
- v1: Create `memory` table with base columns
- v2: Add `scope`, `writer_agent_depth` columns; create indexes

## Remote Initialization

`memory_db_bootstrap_script(mounted_root)` generates a self-contained Python
script that can be executed inside a Daytona sandbox to create the DB remotely.
This is used when the host cannot directly access the sandbox filesystem.
