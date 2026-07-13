# Volume layout (clean)

Root = `FLEET_VOLUME_MOUNT_PATH` (default `/home/daytona/fleet`).

| Relative path | Purpose |
|---------------|---------|
| `skills/` | Optional skill markdown on the volume |
| `memory/` | Reserved for durable key/value style data |
| `artifacts/` | Workspace-level durable outputs |
| `attachments/` | Attachment materialization under the volume when used |
| `sessions/<session_uuid>/` | Session directory |
| `sessions/<session_uuid>/exports/` | Session exports |
| `sessions/<session_uuid>/staging/` | Session staging |
| `sessions/<session_uuid>/runs/<run_uuid>/` | Per-run root |
| `sessions/<session_uuid>/runs/<run_uuid>/staging/` | Run staging |
| `sessions/<session_uuid>/runs/<run_uuid>/artifacts/` | Run-scoped artifacts |

## Notes

- Paths are **logical sandbox paths**, not host filesystem paths.
- Session and run path segments must be UUIDs (`daytona.paths.validate_path_id`).
- Live layout under `/home/daytona/memory/` with `memories/core.db` is **not** the clean contract.
