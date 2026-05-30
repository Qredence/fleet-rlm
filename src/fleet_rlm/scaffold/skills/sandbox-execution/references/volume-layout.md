# Volume Layout

## Durable Phase 1+ Layout (current)

| Path | Purpose |
|------|---------|
| `/home/daytona/memory/memories/` | Versioned persistent memory DB (core.db) |
| `/home/daytona/memory/knowledge/` | Ingested documents, summaries, index.json |
| `/home/daytona/memory/skills/` | Human-curated and bundled markdown skills |
| `/home/daytona/memory/sessions/` | Durable session manifests and scratchpads |
| `/home/daytona/memory/logs/` | Runtime logs |
| `/home/daytona/memory/uploads/` | Uploaded files before ingestion |

## Legacy Roots (migration fallbacks, still read)

| Path | Purpose |
|------|---------|
| `/home/daytona/memory/memory/` | Key-value named memory items |
| `/home/daytona/memory/artifacts/` | Produced outputs and saved results |
| `/home/daytona/memory/buffers/` | Named buffer lists (session logs, staging) |
| `/home/daytona/memory/meta/` | Legacy manifests and workspace metadata |

## Session Manifest Path

Current: `sessions/<session_id>/conversation.json`

Legacy fallback: `meta/workspaces/<workspace_id>/users/<user_id>/react-session-<session_id>.json`
