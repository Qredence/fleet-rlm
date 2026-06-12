# GEPA Pipeline

**GEPA** = Guided Evolution through Prompt Adaptation

A reflective optimization loop that evolves prompts using textual feedback rather than purely scalar metrics. GEPA generates verbal critiques of prompt performance, then rewrites prompts to address identified weaknesses.

---

## Pipeline Overview

```text
Dataset
  → Split (train / dev)
  → Compile (GEPA)
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
| `max_iterations` | Maximum evolution cycles | `5` |
| `feedback_source` | Where to get verbal feedback | `auto` (from scorer explanations) |
| `skill_name` / `skill_path` | Optional seed skill to optimize as markdown instructions | unset |
| `trace_bundle_paths` | Offline trace bundles available to the RLM proposer | unset |

---

## What Gets Logged to MLflow

Each optimization run creates an MLflow run with:

| Artifact | Type | Description |
|----------|------|-------------|
| `optimization_type` | param | `gepa` |
| `module_name` | param | Registered module being optimized |
| `auto_level` | param | Search depth used |
| `train_size` / `dev_size` | param | Dataset split sizes |
| `metrics` | metrics | Dict of scorer name → aggregate score |
| `prompt_before` | artifact | Initial prompt text |
| `prompt_after` | artifact | Optimized prompt text |
| `evaluation_results` | artifact | Per-example scores on dev set |
| `feedback_log` | artifact | GEPA feedback iterations |
| `compile_duration_s` | metric | Time spent in compile phase |

---

## The GEPA Reflective Loop

GEPA uses a verbal feedback cycle:

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

## Skill Artifact Mode

Skill optimization represents seed markdown as the GEPA prompt component. The
instruction proposer can inspect large offline trace bundles and candidate
history, then return revised skill instructions. The optimized file is written
under quality artifacts with a manifest; deployment remains manual.

---

## Running the Pipeline

```bash
# GEPA optimization with report
uv run fleet-rlm optimize grounded_answer ./data/qa_train.jsonl \
    --auto medium --report

# GEPA skill optimization with offline trace context
uv run fleet-rlm optimize skill ./data/skill_cases.jsonl \
    --skill-name optimization \
    --trace-bundle-path ./artifacts/traces/optimization_failures.jsonl \
    --auto medium \
    --report

# Dry run — show what would be optimized without running
uv run fleet-rlm optimize grounded_answer ./data/qa_train.jsonl \
    --dry-run
```
