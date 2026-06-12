---
name: optimization
description: "Optimize fleet-rlm DSPy programs and RLM skill bundles using GEPA with MLflow tracking. Use when running optimization loops, designing feedback metrics, building training datasets, or comparing runs."
---

# GEPA Optimization

## CLI Command

```bash
# Optimize a registered module with auto-level selection
uv run fleet-rlm optimize <module> <dataset> --auto light|medium|heavy --report

# Optimize a markdown skill artifact without overwriting the bundled skill
uv run fleet-rlm optimize skill <dataset> \
    --skill-name optimization \
    --trace-bundle-path artifacts/traces/optimization-failures.jsonl \
    --auto medium \
    --report

# List all optimizable modules registered in the system
uv run fleet-rlm optimize list
```

---

## GEPA-Only Model

GEPA is the only public optimization path. It evolves prompt text from
GEPA-compatible metrics that return a score plus actionable feedback.

**Auto-level behavior:**

| Level | Search depth | Use case |
|-------|-------------|----------|
| `--auto light` | Minimal search, 2-3 candidates | Fast iteration during development |
| `--auto medium` | Balanced search, 5-10 candidates | Standard optimization runs |
| `--auto heavy` | Exhaustive search, 20+ candidates | Production-quality prompt selection |

---

## RLM Skill Optimization

Skill optimization treats a markdown `SKILL.md` bundle as the prompt component
GEPA evolves. The proposer RLM receives current skill text, trace bundle paths,
metric feedback, and candidate history, then returns revised instructions only.
The optimized skill is saved as a quality artifact with manifest metadata; it
never overwrites the source or bundled scaffold skill automatically.

Use `--skill-name` for a bundled scaffold skill, or `--skill-path` for a seed
skill file under the optimization data root. Add `--trace-bundle-path` one or
more times when the proposer needs large offline trace bundles.

---

## Dataset Preparation

**Upload JSON/JSONL:**
```bash
# Upload a dataset file
curl -X POST /api/v1/optimization/datasets \
    -F "file=@training_data.jsonl" \
    -F "name=auth-intent-v2"
```

**Convert transcripts to training data:**
```bash
# Convert conversation transcripts into structured examples
curl -X POST /api/v1/optimization/datasets/from-transcript \
    -H "Content-Type: application/json" \
    -d '{"transcript_ids": ["t_001", "t_002"], "module": "grounded_answer"}'
```

**Programmatic split:**
```python
from fleet_rlm.quality.optimization import rows_to_examples, split_examples

examples = rows_to_examples(raw_data)
train, dev = split_examples(examples)  # default 80/20 split
```

**Load from traces:**
```python
from fleet_rlm.quality.optimization import load_trace_rows

rows = load_trace_rows(
    experiment="fleet-rlm",
    module="grounded_answer",
    min_score=0.7  # Only traces scored above threshold
)
```

---

## MLflow Integration

| Setting | Env Variable | Default |
|---------|-------------|---------|
| Tracking URI | `MLFLOW_TRACKING_URI` | `http://127.0.0.1:5001` |
| Experiment name | `MLFLOW_EXPERIMENT` | `fleet-rlm` |
| Enable tracking | `MLFLOW_ENABLED` | `true` |

**Start MLflow server:**
```bash
make mlflow-server  # Runs on port 5001
```

**What gets auto-logged per optimization run:**
- Compile parameters (GEPA, auto-level, train/dev split ratio)
- Evaluation metrics (per-scorer breakdown, aggregate score)
- Prompt snapshots (before/after optimization)
- Dataset metadata (size, source, schema hash)

**Compare runs:**
```bash
# Compare two optimization runs
curl -X GET "/api/v1/optimization/runs/compare?run_a=<id>&run_b=<id>"
```

---

## Registered Modules

| Module | Domain |
|--------|--------|
| `grounded_answer` | General Q&A with source grounding |
| `memory_tree` | Memory structure decisions |
| `memory_action_intent` | Intent classification for memory ops |
| `memory_structure_migration_plan` | Memory schema evolution planning |
| `clarification_questions` | Generating clarifying questions |
| `triage_incident_logs` | Log analysis and incident routing |

See the **dspy-programs** skill for full module design and registration details.

---

## See Also

- **dspy-programs** — module design, signature definition, and registration
- **diagnostics** — when optimization fails (missing traces, scorer errors, budget exhaustion)
