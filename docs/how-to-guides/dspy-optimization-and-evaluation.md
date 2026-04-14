# DSPy Optimization and Evaluation

This guide covers how to optimize and evaluate worker-native DSPy modules
using the standardized evaluation subsystem in `runtime/quality/`.

## Overview

Fleet-RLM includes several DSPy modules in the worker/runtime layer, each
responsible for a recursive reasoning step such as reflection, decomposition,
context selection, repair, or verification. The evaluation subsystem provides:

- **Central module registry** — single source of truth for optimizable modules
- **Shared dataset helpers** — consistent loading, validation, and splitting
- **Reusable scoring primitives** — composable scoring helpers for metrics
- **GEPA optimization pipeline** — compile → evaluate → save → manifest flow
- **Artifact/manifest discipline** — Daytona-backed storage with local fallback
- **Unified CLI** — `fleet-rlm optimize <module> <dataset>`
- **Frontend module picker** — Optimization page with registry-driven selection

GEPA (Generative Evolution of Prompts with Assessment) runs are compute-intensive
and should not be invoked in the live request path; use the CLI or the async
`POST /api/v1/optimization/runs` endpoint instead.

### Prerequisites

GEPA requires a configured DSPy LM for both module execution and reflection.
Set the following environment variables (or provide a `.env` file):

- `DSPY_LM_MODEL` — primary model identifier (e.g. `openai/gpt-4o`)
- `DSPY_LLM_API_KEY` — API key for the model provider
- `DSPY_DELEGATE_LM_MODEL` *(optional)* — stronger model used for GEPA's
  reflection pass; falls back to `DSPY_LM_MODEL` if not set

## Registered Modules

The following modules are registered in the module registry:

| Slug                | Label                      | Program Spec                                                            |
| ------------------- | -------------------------- | ----------------------------------------------------------------------- |
| `reflect-and-revise`  | Reflect & Revise           | `fleet_rlm.runtime.agent.chat_agent:build_reflect_and_revise_program`  |
| `context-selection`   | Recursive Context Selection | `fleet_rlm.runtime.content.context_assembly:build_context_program`    |
| `decomposition`       | Recursive Decomposition    | `fleet_rlm.runtime.agent.decomposition:build_decomposition_program`   |
| `repair`              | Recursive Repair           | `fleet_rlm.runtime.agent.repair:build_repair_program`                 |
| `verification`        | Recursive Verification     | `fleet_rlm.runtime.agent.verification:build_verification_program`     |

## Dataset Format

Datasets are JSON arrays or JSONL files. Each row is a dict with
module-specific keys.

### JSON array

```json
[
  {"query": "How does auth work?", "expected_actions": ["search", "read"], ...},
  {"query": "Fix the bug in utils.py", ...}
]
```

### JSONL (one JSON object per line)

```jsonl
{"query": "How does auth work?", "expected_actions": ["search", "read"]}
{"query": "Fix the bug in utils.py", "expected_actions": ["edit"]}
```

### Required keys per module

Each module declares `required_dataset_keys` in its spec. Rows missing any
required key are filtered out with a warning. If all rows are filtered, the
loader raises a `ValueError`.

Check required keys via the CLI:

```bash
fleet-rlm optimize list
```

Or via the API:

```bash
curl -s http://localhost:8000/api/v1/optimization/modules | python -m json.tool
```

### Row-to-example conversion

Each module provides a `row_converter` callable that maps raw dataset rows
to DSPy `Example` objects with the correct input keys. The shared helpers
handle loading and validation; the converter handles field mapping.

## Metrics

### Scoring primitives

Reusable scoring functions in `runtime/quality/scoring_helpers.py`:

- **`set_overlap_score(expected, actual)`** — Jaccard-style set overlap (0.0–1.0)
- **`text_presence_score(text)`** — 1.0 if non-empty, 0.0 if empty
- **`boundedness_score(actual_count, budget)`** — 1.0 if within budget
- **`action_match_score(expected, actual)`** — 1.0 on exact match

### ScoreFeedbackBuilder

Optional accumulator for weighted sub-scores:

```python
from fleet_rlm.runtime.quality.scoring_helpers import ScoreFeedbackBuilder

builder = ScoreFeedbackBuilder()
builder.add(0.9, 0.4, "overlap looks good")
builder.add(1.0, 0.3, "within budget")
builder.add(0.0, 0.3, "missing key action")
result = builder.build()  # returns ScoreWithFeedback
```

### Per-module metrics

Each module defines its own `build_*_feedback_metric()` function that returns
a GEPA-compatible metric callable. Metrics may use shared scoring primitives
or implement entirely custom scoring logic.

## Running GEPA Optimization

### CLI

```bash
# Optimize a registered module
fleet-rlm optimize reflect-and-revise traces.json

# With options
fleet-rlm optimize decomposition data.jsonl \
  --output-path optimized_decomp.json \
  --train-ratio 0.75 \
  --auto medium \
  --report

# List available modules
fleet-rlm optimize list
```

### Programmatic

```python
from fleet_rlm.runtime.quality.module_registry import get_module_spec
from fleet_rlm.runtime.quality.optimization_runner import run_module_optimization

spec = get_module_spec("reflect-and-revise")
result = run_module_optimization(
    spec,
    dataset_path="traces.json",
    output_path="optimized.json",
    train_ratio=0.8,
    auto="light",
)
print(result["validation_score"])
```

### Frontend

The Optimization page provides two tabs:

- **New Run** — module picker that auto-populates the program spec and shows
  required dataset keys. Select a module, provide a dataset path, and click
  "Run GEPA". Runs are submitted asynchronously and the UI switches to the
  Run History tab automatically.
- **Run History** — lists all optimization runs with status badges, relative
  timestamps, and a detail panel. Click any run to expand its metadata
  (program spec, optimizer, intensity, train ratio, dataset, phase, duration,
  error, validation score). The list polls automatically while runs are active.

### API

```bash
# Async run (recommended — returns immediately, runs in background)
curl -X POST http://localhost:8000/api/v1/optimization/runs \
  -H "Content-Type: application/json" \
  -d '{"module_slug": "reflect-and-revise", "dataset_path": "traces.json", "auto": "light", "train_ratio": 0.8}'

# List runs (with optional status filter and pagination)
curl -s http://localhost:8000/api/v1/optimization/runs?status=completed&limit=10

# Get single run detail
curl -s http://localhost:8000/api/v1/optimization/runs/1

# Blocking run (backward compatible — waits for completion)
curl -X POST http://localhost:8000/api/v1/optimization/run \
  -H "Content-Type: application/json" \
  -d '{"module_slug": "reflect-and-revise", "dataset_path": "traces.json", "auto": "light", "train_ratio": 0.8}'
```

### Run lifecycle

Async runs progress through these phases:
`loading` → `compiling` → `saving` → `completed`

If any phase fails, the run transitions to `failed` with an error message.
On server restart, any runs left in `running` status are automatically
recovered and marked as `failed` with a "Server restarted" message.

## Artifacts and Manifests

### Storage paths

- **Daytona**: `/home/daytona/memory/artifacts/quality/<module_slug>/`
- **Local fallback**: `.data/quality-artifacts/<module_slug>/`
- **API-triggered runs**: constrained to `OPTIMIZATION_DATA_ROOT` from the router

### Manifest schema

Each optimization run produces a JSON manifest:

```json
{
  "dataset_path": "traces.json",
  "module": "fleet_rlm.runtime.agent.chat_agent:build_reflect_and_revise_program",
  "train_examples": 80,
  "validation_examples": 20,
  "validation_score": 0.847,
  "optimizer": "GEPA",
  "metric": "reflect_and_revise_feedback",
  "auto": "light"
}
```

### Markdown report

Use `--report` with the CLI to print a markdown summary to stdout:

```bash
fleet-rlm optimize reflect-and-revise traces.json --report
```

This prints module info, dataset stats, and validation score.

## Adding Optimization for a New Module

1. **Define the module's optimization entrypoint** in a new file under
   `src/fleet_rlm/runtime/quality/optimize_<name>.py`:

   ```python
   from fleet_rlm.runtime.quality.datasets import load_dataset_rows, validate_required_keys
   from fleet_rlm.runtime.quality.artifacts import resolve_artifact_path
   from fleet_rlm.runtime.quality.module_registry import ModuleOptimizationSpec, register_module
   from fleet_rlm.runtime.quality.optimization_runner import run_module_optimization

   _MODULE_SLUG = "my-module"
   _REQUIRED_DATASET_KEYS = ["query", "expected_output"]
   _INPUT_KEYS = ["query"]

   def rows_to_examples(rows):
       valid = validate_required_keys(rows, _REQUIRED_DATASET_KEYS, "MyModule")
       # Convert to dspy.Example objects
       ...

   def build_metric():
       # Return a GEPA-compatible metric callable
       ...

   _MODULE_SPEC = ModuleOptimizationSpec(
       module_slug=_MODULE_SLUG,
       label="My Module",
       program_spec="fleet_rlm.my_module:build_program",
       artifact_filename="optimized_my_module.json",
       input_keys=_INPUT_KEYS,
       required_dataset_keys=_REQUIRED_DATASET_KEYS,
       module_factory=lambda: ...,
       row_converter=rows_to_examples,
       metric_builder=build_metric,
       metric_name="my_module_feedback",
   )
   register_module(_MODULE_SPEC)
   ```

2. **Register the import** in `module_registry.py`'s `_ensure_registered()`:

   ```python
   try:
       from . import optimize_my_module as _m6  # noqa: F401
   except Exception:
       pass
   ```

3. **Add tests** in `tests/unit/runtime/quality/test_optimize_my_module.py`.

4. **Export from `__init__.py`** if needed for backward compatibility.

The module will automatically appear in the CLI (`fleet-rlm optimize list`),
the API (`GET /api/v1/optimization/modules`), and the frontend module picker.

## Architecture Rules

- DSPy modules stay in the worker/runtime cognition layer
- GEPA runs are compute-intensive — use the CLI or async API, never the live request path
- Agent Framework (`agent_host/`) remains orchestration-only
- FastAPI remains transport-only
- Daytona remains the execution and durable-memory substrate
- Evaluation artifacts live in Daytona-backed quality storage with local fallback
