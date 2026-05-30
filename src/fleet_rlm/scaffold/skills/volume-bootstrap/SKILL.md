---
name: volume-bootstrap
description: "Understand the pre-initialized Daytona volume filesystem. Use when operating inside a sandbox to know what directories exist, where to store state, how the memory DB works, and what guarantees hold across sessions."
---

# Volume Bootstrap — In-Sandbox Filesystem Contract

When you land inside a Daytona sandbox with a volume attached, the filesystem
is already initialized. This skill explains what's there and how to use it.

## What's Already Done For You

When `volume_name` is set on the interpreter, the runtime automatically runs:

1. `ensure_daytona_volume_layout()` — creates all canonical directories
2. `init_memory_db()` — bootstraps SQLite schema at `memories/core.db`
3. `seed_system_skills()` — populates `skills/system/` with bundled fleet-rlm skills

You do NOT need to `mkdir` any standard directories. They exist.

## Filesystem Map

```text
MEMORY_ROOT/                            ← /home/daytona/memory/
├── memories/
│   └── core.db                         ← SQLite persistent memory (auto-created)
├── knowledge/
│   ├── ingested/                       ← Raw ingested files
│   └── summaries/                      ← Generated summaries
├── skills/
│   ├── system/                         ← Auto-seeded from fleet-rlm package
│   └── user/                           ← User-added custom skills
├── sessions/<session_id>/
│   └── conversation.json               ← Durable session manifest
├── logs/                               ← Runtime logs
├── uploads/                            ← Pre-ingestion file staging
├── memory/                             ← (durable) key-value items
├── artifacts/                          ← (durable) produced outputs
├── buffers/                            ← (durable) named buffer lists
└── meta/                               ← (durable) workspace metadata
```

`MEMORY_ROOT` is injected as a global in sandbox code. Use it instead of hardcoding paths.

## CRUD Contract (Inside Sandbox)

### Persistent Memory (cross-session)

```python
# Write a fact (root agent only — depth-gated)
remember("project_language", "Python 3.12")

# Search memory (any agent depth can read)
results = recall("language")
# Returns up to 50 matches from core.db
```

### Session Buffers (same session only)

```python
# Accumulate items across execute() calls
add_buffer("findings", "First observation")
add_buffer("findings", "Second observation")

# Retrieve later in same session
items = get_buffer("findings")  # ["First observation", "Second observation"]
```

### Document & Knowledge Access

```python
# Load ingested document
content = load_document("knowledge/ingested/report.pdf")

# Search knowledge base
results = search_knowledge("deployment architecture")

# Load a bundled skill
skill_text = load_skill("rlm")
```

### Direct File I/O (for custom artifacts)

```python
import json
from pathlib import Path

out = Path(MEMORY_ROOT) / "artifacts" / "analysis.json"
out.write_text(json.dumps({"status": "complete", "findings": [...]}))
```

## Statefulness Guarantees

| What | Persists across sessions? |
|------|--------------------------|
| Everything under `MEMORY_ROOT/` | Yes (same `volume_name`) |
| `memories/core.db` entries | Yes |
| Files in `knowledge/`, `artifacts/`, `logs/` | Yes |
| Session manifests in `sessions/` | Yes |
| Workspace files (cloned repos, temp code) | No — transient |
| Buffer contents (`add_buffer`) | No — session-scoped |
| Python variables and interpreter state | No — per-execute |

## Write Conventions

| Data type | Write to | Read back via |
|-----------|----------|---------------|
| Factual memory | `remember(key, value)` | `recall(query)` |
| Generated artifacts | `MEMORY_ROOT/artifacts/<name>` | direct file read |
| Ingested documents | `MEMORY_ROOT/knowledge/ingested/<name>` | `load_document(path)` |
| Temporary staging | `MEMORY_ROOT/uploads/` | process then move to `knowledge/` |
| Structured logs | `MEMORY_ROOT/logs/<name>.jsonl` | direct file read |
| Session scratchpad | `MEMORY_ROOT/sessions/<id>/scratch.json` | direct file read |

## Path Security

- All API-layer access validated against `VFS_CANONICAL_ROOTS`
- Path traversal (`..`, `%2e%2e`, `%2f`, `%5c`) is rejected
- Only canonical roots accessible via the volume API
- Sandbox code has full filesystem access (security boundary is the sandbox itself)

## Depth-Gating Rules

| Operation | Root agent (depth=0) | Child agent (depth>0) |
|-----------|---------------------|----------------------|
| `remember(key, value)` | Allowed | Blocked (read-only) |
| `recall(query)` | Allowed | Allowed |
| Direct file write to volume | Allowed | Allowed (if `context` isolation mode shares volume) |
| `add_buffer` / `get_buffer` | Allowed | Allowed (own session scope) |

## See Also

- **sandbox-execution** — Interpreter lifecycle, DaytonaInterpreter config, host-side perspective
- **diagnostics** — Volume troubleshooting (data missing, mount failures)
