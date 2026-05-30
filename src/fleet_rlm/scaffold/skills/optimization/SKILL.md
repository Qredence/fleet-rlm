---
name: optimization
description: "Optimize fleet-rlm DSPy programs using GEPA or MIPROv2 with MLflow tracking. Use when running optimization loops, designing evaluation metrics, building training datasets, or comparing runs."
---

# DSPy Program Optimization

## CLI Command

```bash
# Optimize a module with auto-level selection
uv run fleet-rlm optimize <module> <dataset> --auto light|medium|heavy --report

# List all optimizable modules registered in the system
uv run fleet-rlm optimize list
```

---

## GEPA vs MIPROv2 Decision

| Criteria | GEPA | MIPROv2 |
|----------|------|---------|
| Text feedback available | Yes — reflective evolution from verbal feedback | No |
| Scalar metrics only | No | Yes — instruction/few-shot optimization |
| Prompt evolution needed | Yes — iterative prompt rewriting via feedback | No |
| Quick instruction tuning | No | Yes — fast convergence on instruction variants |

**Auto-level behavior:**

| Level | Search depth | Use case |
|-------|-------------|----------|
| `--auto light` | Minimal search, 2-3 candidates | Fast iteration during development |
| `--auto medium` | Balanced search, 5-10 candidates | Standard optimization runs |
| `--auto heavy` | Exhaustive search, 20+ candidates | Production-quality prompt selection |

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
- Compile parameters (optimizer type, auto-level, train/dev split ratio)
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
