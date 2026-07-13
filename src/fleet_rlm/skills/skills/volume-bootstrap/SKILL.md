---
name: volume-bootstrap
description: "Clean Daytona volume filesystem contract under the Fleet mount. Use when writing or reading paths inside the sandbox volume."
---

# Volume bootstrap (clean)

When a volume is attached, treat the mount root as the only durable workspace root.

Default mount: `/home/daytona/fleet` (`FLEET_VOLUME_MOUNT_PATH`).

## Filesystem map

```text
MOUNT/                              ← e.g. /home/daytona/fleet
├── skills/                         ← optional volume-side skills
├── memory/                         ← reserved durable store
├── artifacts/                      ← workspace artifacts
├── attachments/                    ← attachment materialization
└── sessions/<session_uuid>/
    ├── exports/
    ├── staging/
    └── runs/<run_uuid>/
        ├── staging/
        └── artifacts/
```

Use UUID session/run ids only. Do not invent free-form path segments.

## What clean does **not** provide (yet)

- Auto `init_memory_db()` / SQLite `memories/core.db`
- REPL helpers `remember` / `recall` / `search_knowledge` / `load_document`
- Live `seed_system_skills()` into `/home/daytona/memory/skills/system`
- Depth-gated multi-agent memory writes

Host progressive skills use `load_skill(skill_id)` with UUIDs from SkillCards — not volume `load_skill("name")`.

## Safe file I/O pattern

```python
from pathlib import Path

mount = Path("/home/daytona/fleet")  # or settings mount
out = mount / "sessions" / session_id / "runs" / run_id / "artifacts" / "analysis.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(payload, encoding="utf-8")
```

Prefer host `create_artifact` when tools are bound — host store paths must never appear in client API responses.

## Persistence

| What | Durable across sessions? |
|------|--------------------------|
| Paths under the volume mount | Yes (same `volume_name`) |
| Interpreter REPL variables / context | No (per lease / context) |
| Host attachment/artifact stores | Yes (host disk; separate from volume) |

## Path security

- Host APIs validate path ids and reject traversal.
- Sandbox code can still write broadly; treat credentials and env as out-of-scope for exfiltration.

## See also

- **sandbox-execution** — interpreter lifecycle
- `references/filesystem-contract.md` — path rules detail
