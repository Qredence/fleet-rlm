# fleet-rlm — Bundled Claude Code Skills

These skills ship inside the `fleet-rlm` Python package. They are reference
documents that explain how to drive fleet-rlm's recursive DSPy + Daytona
runtime from Claude Code (or any agent that loads skill markdown).

## What's Here

| Skill                 | Purpose                                                            |
| --------------------- | ------------------------------------------------------------------ |
| `rlm`                 | Top-level mental model — ReAct + `dspy.RLM` over Daytona sandboxes |
| `daytona-runtime`     | Daytona execution path: volume layout, repo staging, smoke test    |
| `daytona-sandbox`     | Sandbox lifecycle, interpreter surface, stateful execution         |
| `dspy-signature`      | Writing `dspy.Signature` subclasses for RLM tasks                  |
| `rlm-execute`         | Running Python in a Daytona sandbox with durable persistence       |
| `rlm-long-context`    | Decomposing documents that exceed a single context window          |
| `rlm-batch`           | Batched recursive work with a shared LLM-call budget               |
| `rlm-memory`          | Session memory, core memory, and durable volume layout             |
| `rlm-run`             | End-to-end RLM invocation with parent + child bounds               |
| `rlm-test-suite`      | Regression patterns for `dspy.RLM` programs                        |
| `rlm-debug`           | Failure diagnosis and contract debugging                           |

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

## How to Consume Them from Claude Code

There are two common patterns:

1. **Point Claude Code at the package location.** After `uv add fleet-rlm`,
   resolve the skills root once and add it to your Claude Code skills search
   path:

   ```bash
   python -c "from importlib.resources import files; \
       print(files('fleet_rlm.scaffold') / 'skills')"
   ```

   Use the printed path in your Claude Code configuration.

2. **Copy a specific skill into your project.** If you want a skill to live
   alongside your own project's skills (for local edits), copy just the one
   you need:

   ```bash
   python -c "
   from importlib.resources import files
   from pathlib import Path
   import shutil
   src = files('fleet_rlm.scaffold') / 'skills' / 'rlm-long-context'
   shutil.copytree(src, Path('.claude/skills/rlm-long-context'))
   "
   ```

   Local copies will not receive fleet-rlm package updates.

## When to Use Which Skill

- Starting a new fleet-rlm workflow? Load `rlm` first — it frames the mental
  model and points at the others.
- Driving execution in a specific Daytona sandbox? `daytona-runtime` +
  `rlm-execute`.
- Processing a document larger than one context window? `rlm-long-context`.
- Something broke at runtime? `rlm-debug`, then narrow from there.

## Stability

These skills document `fleet-rlm`'s runtime API at the version they ship
with. The surface they describe — `FleetAgent`, `dspy.RLM`, `DaytonaInterpreter`,
`delegate_to_rlm()`, `sub_rlm()` — is the canonical runtime contract of the
repo. If you find a skill referencing a module or function that no longer
exists, file an issue: the skill is out of date relative to its shipped
version.
