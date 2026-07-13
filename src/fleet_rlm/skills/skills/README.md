# fleet_rlm — Bundled Agent Skills

Reference skills for the **clean** parallel backend: `dspy.RLM` + Daytona
`code_interpreter` + SSE `POST /api/chat`. They are not the live
`fleet_rlm.scaffold` skill set (WebSocket, EscalatingFleet, GEPA).

## Catalog

| Skill | Purpose | Model-invoked |
|-------|---------|---------------|
| `rlm` | Hub: budgets, SSE surface, next skill | yes |
| `sandbox-execution` | Interpreter lease + volume settings | yes |
| `volume-bootstrap` | Mount layout contract | yes |
| `long-context` | Variable-mode large inputs | yes |
| `diagnostics` | Operator failure trees | yes |
| `browser-interaction` | Optional Playwright / SPA pages | yes |
| `writing-great-skills` | Authoring principles | **no** (`disable-model-invocation`) |

Removed from clean seed (live-only sediment): `delegation`, `dspy-programs`,
`optimization`.

## Shipping

Package data under `fleet_rlm.skills.skills`. Seed into the host registry:

```python
from fleet_rlm.skills.loader import seed_bundled_skills
from fleet_rlm.skills.registry import InMemorySkillRegistry

registry = InMemorySkillRegistry()
seed_bundled_skills(registry)
```

Do not hardcode install paths. Prefer `importlib.resources` via the loader.

## Consumption

1. **API:** `GET /api/skills` returns SkillCards (metadata only).
2. **Turn tools:** `load_skill(skill_id)` / `read_skill_resource` after host authorize.
3. **Humans / agents editing skills:** read this tree on disk; follow
   `writing-great-skills` (user-invoked only).

## Stability

Skills must describe `fleet_rlm` only. If a skill references
`fleet_rlm.runtime`, WebSocket `/api/v1/ws/execution`, or GEPA CLI, it is stale.
