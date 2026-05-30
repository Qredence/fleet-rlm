# GEPA Pipeline

**GEPA** = Guided Evolution through Prompt Adaptation

A reflective optimization loop that evolves prompts using textual feedback rather than purely scalar metrics. GEPA generates verbal critiques of prompt performance, then rewrites prompts to address identified weaknesses.

---

## Pipeline Overview

```text
Dataset
  → Split (train / dev)
  → Compile (MIPROv2 or GEPA)
  → Evaluate (scorers on dev set)
  → Persist artifacts (MLflow + local)
```

---

## Configuration Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `train_ratio` | Fraction of dataset for training | `0.8` |
| `auto_level` | Search depth: `light`, `medium`, `heavy` | `medium` |
| `output_path` | Where to save optimized program | `.fleet_rlm/optimized/<module>.json` |
| `report` | Generate HTML comparison report | `false` |
| `optimizer` | `gepa` or `miprov2` | Auto-selected based on feedback availability |
| `max_iterations` | Maximum evolution cycles (GEPA only) | `5` |
| `feedback_source` | Where to get verbal feedback | `auto` (from scorer explanations) |

---

## What Gets Logged to MLflow

Each optimization run creates an MLflow run with:

| Artifact | Type | Description |
|----------|------|-------------|
| `optimization_type` | param | `gepa` or `miprov2` |
| `module_name` | param | Registered module being optimized |
| `auto_level` | param | Search depth used |
| `train_size` / `dev_size` | param | Dataset split sizes |
| `metrics` | metrics | Dict of scorer name → aggregate score |
| `prompt_before` | artifact | Initial prompt text |
| `prompt_after` | artifact | Optimized prompt text |
| `evaluation_results` | artifact | Per-example scores on dev set |
| `feedback_log` | artifact | GEPA feedback iterations (GEPA only) |
| `compile_duration_s` | metric | Time spent in compile phase |

---

## The GEPA Reflective Loop

GEPA differs from MIPROv2 by using a verbal feedback cycle:

```text
1. Initial prompt
   ↓
2. Evaluate on train set → per-example scores
   ↓
3. Generate feedback: "The prompt fails on X because Y"
   ↓
4. Evolve prompt: rewrite addressing feedback
   ↓
5. Re-evaluate on train set
   ↓
6. If improved → accept; if not → revert and try different feedback
   ↓
7. Repeat until max_iterations or convergence
   ↓
8. Final evaluation on held-out dev set
```

**Feedback generation uses:**
- Low-scoring examples (bottom 20%) as failure cases
- High-scoring examples (top 20%) as success reference
- Scorer explanations when available (e.g., LLM judge rationales)

**Convergence criteria:**
- Score improvement < 1% for 2 consecutive iterations
- All dev-set examples pass above threshold
- Max iterations reached

---

## MIPROv2 Comparison

When GEPA is not applicable (no textual feedback available), the pipeline falls back to MIPROv2:

| Aspect | GEPA | MIPROv2 |
|--------|------|---------|
| Optimization target | Full prompt text | Instructions + few-shot examples |
| Feedback type | Verbal critique | Scalar metric only |
| Search method | Reflective rewriting | Bayesian candidate search |
| Iterations | 3-5 typically sufficient | 5-20 candidates evaluated |
| Best for | Complex reasoning prompts | Instruction tuning, few-shot selection |

---

## Running the Pipeline

```bash
# GEPA optimization with report
uv run fleet-rlm optimize grounded_answer ./data/qa_train.jsonl \
    --auto medium --report

# Force MIPROv2 even if feedback is available
uv run fleet-rlm optimize grounded_answer ./data/qa_train.jsonl \
    --optimizer miprov2 --auto heavy

# Dry run — show what would be optimized without running
uv run fleet-rlm optimize grounded_answer ./data/qa_train.jsonl \
    --dry-run
```
