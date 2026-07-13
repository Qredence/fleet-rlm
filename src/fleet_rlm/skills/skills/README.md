# fleet_rlm — Bundled Agent Skills

Bundled skills for the canonical backend: `dspy.RLM` + Daytona
`code_interpreter` + session-scoped Turn SSE.

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

Delegation, DSPy-program generation, and optimization are not bundled
capabilities.

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

Skills must describe the canonical `fleet_rlm` modules and public HTTP/SSE
contract only.
