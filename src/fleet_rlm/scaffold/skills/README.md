# fleet-rlm — Bundled Agent Skills

These skills ship inside the `fleet-rlm` Python package. They are reference
documents that explain how to drive fleet-rlm's recursive DSPy + Daytona
runtime from Claude Code, Codex, or any agent that loads skill markdown.

## What's Here

| Skill               | Purpose                                                              |
| ------------------- | -------------------------------------------------------------------- |
| `rlm`              | Hub router — core model + decision tree pointing to the right skill  |
| `sandbox-execution` | Execute Python in Daytona sandboxes with durable volume persistence  |
| `delegation`        | Recursive child RLMs, batch fan-out, budget management               |
| `dspy-programs`     | Signature design, module registry, execution mode selection           |
| `long-context`      | Chunking strategies, variable-mode, hierarchical map-reduce          |
| `optimization`      | GEPA/MIPROv2 optimization loops, evaluation metrics, MLflow tracking |
| `diagnostics`       | Symptom→cause decision tree, test lane selection, error catalog       |
| `volume-bootstrap`  | In-sandbox volume filesystem contract, CRUD helpers, persistence guarantees |

Each skill is a directory containing a `SKILL.md` (with YAML frontmatter)
plus optional `references/` and `scripts/` subdirectories.

## How They're Shipped

These are package-data — they travel with the `fleet-rlm` wheel. The install
location on disk depends on where `fleet-rlm` was installed, so consumers
should not hardcode paths. Use `importlib.resources`:

```python
from importlib.resources import files

skills_root = files("fleet_rlm.scaffold") / "skills"

for skill_dir in skills_root.iterdir():
    if skill_dir.is_dir():
        skill_md = skill_dir / "SKILL.md"
        print(skill_dir.name, "->", skill_md.read_text()[:80])
```

## How to Consume Them from an Agent

There are two common patterns:

1. **Point the agent at the package location.** After `uv add fleet-rlm`,
   resolve the skills root once and add it to your agent's skills search
   path:

   ```bash
   python -c "from importlib.resources import files; \
       print(files('fleet_rlm.scaffold') / 'skills')"
   ```

   Use the printed path in your agent configuration.

2. **Copy a specific skill into your project.** If you want a skill to live
   alongside your own project's skills (for local edits), copy just the one
   you need:

   ```bash
   python -c "
   from importlib.resources import files
   from pathlib import Path
   import shutil
   src = files('fleet_rlm.scaffold') / 'skills' / 'long-context'
   shutil.copytree(src, Path('.codex/skills/long-context'))
   "
   ```

   Local copies will not receive fleet-rlm package updates.

## When to Use Which Skill

Start with `rlm` — it contains a decision tree that routes you to the right
workflow skill based on what you're trying to accomplish:

- **Execute code** → `sandbox-execution`
- **Delegate recursively** → `delegation`
- **Design signatures/modules** → `dspy-programs`
- **Process large documents** → `long-context`
- **Optimize programs** → `optimization`
- **Diagnose failures** → `diagnostics`

## Stability

These skills document `fleet-rlm`'s runtime API at the version they ship
with. The surface they describe — `AgentRuntime`, `EscalatingFleetModule`,
`dspy.RLM`, `DaytonaInterpreter`, `delegate_to_rlm()`, `sub_rlm()` — is the
canonical runtime contract. If you find a skill referencing a module or
function that no longer exists, file an issue: the skill is out of date
relative to its shipped version.
