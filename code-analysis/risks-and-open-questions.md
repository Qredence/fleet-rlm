# Risks And Open Questions

## Highest-Risk Areas

### 1. Benchmark Claims May Outrun Evidence

Risk:

The current LongCoT Mini artifacts show promising RLM performance, but some research-report claims are stronger than the evidence supports.

Evidence:

- `output/longcot-eval/final/direct_eval.json`
- `output/longcot-eval/final/rlm_eval.json`
- `output/longcot-eval/archive/longcot-rlm-transport-summary.json`
- `research/longcot-benchmark-findings.md`
- `research/COMPREHENSIVE_COMPARISON_REPORT.md`
- `mlflow_verification_report.md`

Concerns:

- Transport summary covers 55 tasks, not all 100.
- MLflow traces are not reliably linked to the benchmark run.
- Wrong-formatting interpretation is overstated.
- Statistical significance is asserted without visible test provenance.
- Chemistry regresses from 6/20 direct correct to 0/20 RLM correct.

Open questions:

- What exact evaluator version and commit produced the final JSON files?
- Were direct and RLM runs executed with the same model, provider, retry policy, and token budget?
- Which per-task traces correspond to each final output row?
- Should benchmark claims use overall accuracy, failure-excluded accuracy, or both?

## 2. GEPA Metric And Benchmark Evaluator Are Not The Same

Risk:

The LongCoT GEPA metric may optimize answer similarity and reasoning style without improving official LongCoT evaluator correctness.

Evidence:

- `src/fleet_rlm/runtime/quality/optimize_longcot.py`
- `scripts/generate_longcot_gepa_dataset.py`
- `scripts/run_longcot_eval.py`

Concerns:

- Reasoning score can reward plausible-looking reasoning even when the answer is wrong.
- The GEPA dataset excludes `logic`, which is the strongest observed RLM benchmark domain.
- The official evaluator and GEPA surrogate metric likely disagree on structured answer validity.

Open questions:

- What is the correlation between GEPA surrogate score and LongCoT evaluator correctness?
- Should each LongCoT domain have a separate metric or formatter?
- Should GEPA optimize for evaluator-compatible final-answer extraction instead of freeform reasoning quality?

## 3. Optimization Product Contract Is Ambiguous

Risk:

Users and contributors cannot tell whether MLflow is required for GEPA execution or only for telemetry/artifact tracking.

Evidence:

- `src/fleet_rlm/runtime/quality/optimization_runner.py`
- `src/fleet_rlm/runtime/quality/gepa_optimization.py`
- `src/fleet_rlm/api/routers/optimization/status.py`
- `src/fleet_rlm/api/routers/optimization/runs.py`
- `src/frontend/src/features/optimization/components/optimization-form.tsx`

Concerns:

- Offline module runner exists.
- API status hard-gates availability on MLflow.
- Frontend disables run creation when backend status is unavailable.

Open questions:

- Should local GEPA runs work without MLflow?
- Should MLflow be required only for MLflow dataset sources?
- What is the minimum persistence requirement for Optimization UI runs?

## 4. Recursive Runtime Complexity Could Slow Future Work

Risk:

Further RLM, benchmark, or GEPA changes may become fragile because core recursive behavior is concentrated in very large modules.

Evidence:

- `src/fleet_rlm/runtime/models/builders.py`
- `src/fleet_rlm/runtime/tools/rlm_delegate.py`
- `src/fleet_rlm/runtime/agent/runtime.py`

Concerns:

- Many responsibilities per file.
- Failure classification is scattered.
- Runtime-to-Daytona layering is not perfectly clean.
- Benchmarks call deep runtime components directly, bypassing full workbench transport.

Open questions:

- Which recursive runtime behaviors are public contracts versus implementation details?
- Should benchmark runners use the same runtime factory as WebSocket workbench turns?
- Where should evidence persistence be injected?

## 5. Dataset Resolution Has Performance And Safety Concerns

Risk:

The optimization API may recursively scan too broad a directory tree when resolving datasets.

Evidence:

- `OPTIMIZATION_DATA_ROOT` in `src/fleet_rlm/api/routers/optimization/_deps.py`
- `_find_dataset_under_root()` in `src/fleet_rlm/api/routers/optimization/_deps.py`

Concerns:

- Default root is current working directory.
- Repository can contain `output/`, `mlartifacts/`, `vendor/`, and other large trees.
- File discovery may become slow or surprising.

Open questions:

- Should the API accept only registered dataset IDs in non-local environments?
- Should path-based datasets require explicit opt-in root configuration?
- Should dataset upload be the canonical UI path?

## Documentation And Contract Risks

### Supported Surfaces Are Documented Inconsistently

Evidence:

- `README.md`
- `.github/copilot-instructions.md`
- `AGENTS.md`
- `src/frontend/AGENTS.md`
- `src/frontend/src/routes/app/history.tsx`

Risk:

Future contributors may remove or ignore History, or accidentally revive retired routes.

Open question:

- Is History officially supported in the same way as Workbench, Volumes, Optimization, and Settings?

### Optimization Docs Reference Older Modules

Evidence:

- `docs/how-to-guides/dspy-optimization-and-evaluation.md`
- `src/fleet_rlm/runtime/quality/module_registry.py`
- `src/frontend/src/features/optimization/components/datasets-tab.tsx`

Risk:

Users may try to optimize modules that are no longer registered.

Open question:

- Are old modules intentionally retired, or should they be re-registered?

### Python Version Support Is Inconsistent

Evidence:

- `pyproject.toml`
- `AGENTS.md`

Risk:

Users may attempt unsupported Python 3.10 installs.

Open question:

- Is Python 3.10 still supported, or should docs and classifiers be updated to Python 3.11+ only?

## Reliability Risks

### Local Store Has Broad Responsibility

Evidence:

- `src/fleet_rlm/integrations/local_store.py`

Risk:

The local SQLite store handles sessions, datasets, evaluation results, prompt snapshots, optimization runs, and migrations in one large file.

Open questions:

- Should local optimization persistence move into a separate module?
- Should migration behavior be tested with older local store fixtures?

### Child Trace Persistence May Be Event-Loop Sensitive

Evidence:

- `_persist_child_trace()` in `src/fleet_rlm/runtime/tools/rlm_delegate.py`

Risk:

The helper uses synchronous execution around async persistence. It may be safe in current call contexts, but recursive runtime paths are sensitive to event loop boundaries.

Open questions:

- Are child trace persistence calls always outside an active event loop?
- Should trace persistence use the repository's async compatibility helper or an injected async task sink?

### Benchmark Tests May Depend On Local Vendor State

Evidence:

- `tests/unit/test_run_longcot_eval.py`
- `vendor/longcot/`

Risk:

Fresh checkouts or CI jobs may not have the expected vendored benchmark files.

Open questions:

- Is `vendor/longcot` intended to be committed and maintained?
- Should LongCoT tests skip when vendor assets are missing?
- Should tests use minimal fixtures instead of real vendor configs?

## Product Questions To Resolve Before A Rewrite

1. Is Optimization a local developer workflow, a production user-facing workflow, or both?
2. Is MLflow required for Optimization, or only for trace/logging/reporting?
3. Which modules are officially optimizable today?
4. Should LongCoT be a first-class benchmark lane like S-NIAH and OOLONG?
5. Should GEPA optimize freeform reasoning, final-answer extraction, or evaluator-compatible structured output?
6. Is History an official product surface?
7. What is the supported Python version range for the next release?
8. Are generated benchmark artifacts intended to be versioned, ignored, or published separately?
9. Should benchmark runners exercise the full API/WebSocket runtime or lower-level runtime modules?
10. What trace-linking contract is required before benchmark results are considered release-quality?

## Suggested Decision Order

1. Decide MLflow versus offline-first optimization semantics.
2. Decide official optimizable module list.
3. Fix frontend/backend module registry drift.
4. Stabilize LongCoT benchmark reproducibility.
5. Decide whether LongCoT is a release-quality benchmark lane.
6. Refactor recursive runtime hot spots.
7. Update docs, AGENTS files, OpenAPI, and frontend generated clients if API schemas change.

This order avoids a broad rewrite before the product contract is settled.
