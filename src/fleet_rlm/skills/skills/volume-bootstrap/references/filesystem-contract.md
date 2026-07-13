# Filesystem contract (clean)

Owned by `fleet_rlm.daytona.paths.VolumePaths`.

## Rules

1. Mount path must be absolute, not `/`, not under prohibited system prefixes (`/proc`, `/sys`, `/etc`, …).
2. Session and run directory names must be UUID-shaped.
3. Joins that escape the mount root raise `UnsafePathError`.
4. Logical volume paths are never raw host upload/artifact roots.

## Canonical builders

| Method | Result under mount |
|--------|--------------------|
| `skills_root()` | `skills/` |
| `memory_root()` | `memory/` |
| `artifacts_root()` | `artifacts/` |
| `attachments_root()` | `attachments/` |
| `session_dir(session_id)` | `sessions/<uuid>/` |
| `run_dir(session_id, run_id)` | `sessions/<uuid>/runs/<uuid>/` |
| `run_staging_dir(...)` | `.../runs/<uuid>/staging/` |
| `run_artifacts_dir(...)` | `.../runs/<uuid>/artifacts/` |

## Host vs volume

| Store | Location | Client-visible? |
|-------|----------|-----------------|
| Attachments | `FLEET_DATA_ROOT/attachments` in hermetic mode | Metadata + ids only |
| Artifacts | `FLEET_DATA_ROOT/artifacts` in hermetic mode | Metadata + ids only |
| Volume files | Daytona mount | Sandbox-only; not as host paths in API |
