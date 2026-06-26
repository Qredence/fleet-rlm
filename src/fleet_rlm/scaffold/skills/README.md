# fleet-rlm — Bundled Agent Skills

These skills ship inside the `fleet-rlm` Python package. They are reference
documents that explain how to drive fleet-rlm's recursive DSPy + Daytona
runtime from Claude Code, Codex, or any agent that loads skill markdown.

## What's Here

### Runtime & execution skills

| Skill                 | Purpose                                                              |
| --------------------- | -------------------------------------------------------------------- |
| `rlm`                | Hub router — core model + decision tree pointing to the right skill  |
| `sandbox-execution` | Execute Python in Daytona sandboxes with durable volume persistence  |
| `delegation`        | Recursive child RLMs, batch fan-out, budget management               |
| `dspy-programs`     | Signature design, module registry, execution mode selection           |
| `long-context`      | Chunking strategies, variable-mode, hierarchical map-reduce          |
| `optimization`      | GEPA optimization loops, RLM skill artifacts, evaluation metrics, MLflow tracking |
| `diagnostics`       | Symptom→cause decision tree, test lane selection, error catalog       |
| `volume-bootstrap`  | In-sandbox volume filesystem contract, CRUD helpers, persistence guarantees |
| `daytona`           | Create and manage isolated Daytona cloud sandboxes (secure compute, SDK/API/CLI ops) |
| `browser-interaction` | Fetch and inspect JavaScript-heavy pages with Playwright in a Daytona browser-capable snapshot |

### MLflow observability skills

| Skill                            | Purpose                                                              |
| -------------------------------- | -------------------------------------------------------------------- |
| `mlflow-agent`                  | Master dispatcher for all MLflow workflows — routes to the right sub-skill |
| `mlflow-onboarding`             | Get started with MLflow: use-case detection, quickstart, integration  |
| `instrumenting-with-mlflow-tracing` | Add MLflow Tracing to Python/TS code (LangGraph, OpenAI, DSPy, etc.) |
| `retrieving-mlflow-traces`      | Get/search/filter traces by ID, status, tags, execution time         |
| `analyzing-mlflow-trace`        | Debug a single trace by ID — root-cause errors, investigate behavior  |
| `analyzing-mlflow-session`      | Debug a multi-turn chat session — find where a conversation went wrong |
| `querying-mlflow-metrics`       | Aggregated trace metrics: token usage, latency, costs, quality scores |
| `searching-mlflow-docs`         | Search and retrieve official MLflow documentation                     |

### Skill-authoring reference

| Skill                 | Purpose                                                              |
| --------------------- | -------------------------------------------------------------------- |
| `writing-great-skills` | Vocabulary and principles for writing predictable, well-structured skills |

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
- **Provision/manage a sandbox** → `daytona`
- **Scrape or inspect a JS-heavy page** → `browser-interaction`

For MLflow observability work, start with `mlflow-agent` — it dispatches to
the right sub-skill so you don't have to pick:

- **Get started with MLflow** → `mlflow-onboarding`
- **Add tracing to code** → `instrumenting-with-mlflow-tracing`
- **Get/search traces** → `retrieving-mlflow-traces`
- **Debug one trace** → `analyzing-mlflow-trace`
- **Debug a chat session** → `analyzing-mlflow-session`
- **Query token/cost/latency metrics** → `querying-mlflow-metrics`
- **Look up MLflow docs/API** → `searching-mlflow-docs`

When authoring or revising a skill of your own, consult
`writing-great-skills` for the shared vocabulary and principles.

## Stability

These skills document `fleet-rlm`'s runtime API at the version they ship
with. The surface they describe — `AgentRuntime`, `EscalatingFleetModule`,
`dspy.RLM`, `DaytonaInterpreter`, `delegate_to_rlm()`, `sub_rlm()` — is the
canonical runtime contract. If you find a skill referencing a module or
function that no longer exists, file an issue: the skill is out of date
relative to its shipped version.
