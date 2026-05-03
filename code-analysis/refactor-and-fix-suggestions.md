# Refactor And Fix Suggestions

This file lists concrete changes that would improve maintainability, benchmark reliability, and GEPA integration. Each item includes rationale, expected benefit, tradeoff, likely affected files, and priority.

## 1. Make Optimization Availability Semantics Explicit

Priority: high.

Affected files:

- `src/fleet_rlm/api/routers/optimization/status.py`
- `src/fleet_rlm/api/routers/optimization/runs.py`
- `src/fleet_rlm/api/schemas/optimization.py`
- `src/frontend/src/features/optimization/components/optimization-form.tsx`
- `src/frontend/src/features/optimization/optimization-screen.tsx`
- `docs/how-to-guides/dspy-optimization-and-evaluation.md`

Rationale:

The registry runner in `src/fleet_rlm/runtime/quality/optimization_runner.py` is offline-capable, but API status and run validation require MLflow. This makes the product behavior unclear.

Expected benefit:

- Users can understand whether GEPA itself is unavailable or only MLflow logging is unavailable.
- Offline/local optimization can work without false UI blocking.
- API behavior matches runner architecture.

Tradeoff:

- Requires OpenAPI schema updates and frontend client sync if status fields change.

Recommended shape:

- `gepa_available: bool`
- `module_optimization_available: bool`
- `mlflow_logging_available: bool`
- `mlflow_dataset_optimization_available: bool`
- `reasons: string[]`

## 2. Consolidate Blocking And Async Optimization Execution

Priority: high.

Affected files:

- `src/fleet_rlm/api/routers/optimization/runs.py`
- `src/fleet_rlm/api/routers/optimization/background.py`
- `src/fleet_rlm/api/routers/optimization/_deps.py`
- `src/fleet_rlm/runtime/quality/optimization_runner.py`
- `tests/ui/server/test_gepa_e2e_api.py`
- `tests/ui/server/test_optimization_mlflow.py`

Rationale:

The route layer currently has duplicated validation, dataset resolution, run lifecycle, and GEPA execution behavior.

Expected benefit:

- Fewer behavioral differences between blocking and async runs.
- Easier cancellation/recovery behavior.
- One place to add MLflow optionality, artifact policy, and path safety.

Tradeoff:

- Moderate refactor that touches API tests.

Recommendation:

Introduce an `OptimizationService` that owns run creation, dataset resolution, runner invocation, persistence, artifact handling, and optional telemetry.

## 3. Replace Static Frontend Module Slugs With Registry Data

Priority: high.

Affected files:

- `src/frontend/src/features/optimization/components/datasets-tab.tsx`
- `src/frontend/src/features/optimization/components/optimization-form.tsx`
- `src/frontend/src/lib/rlm-api/optimization.ts`
- `src/frontend/src/features/optimization/__tests__/optimization-form.test.tsx`

Rationale:

`datasets-tab.tsx` contains stale slugs for older modules and does not include `longcot-reasoner`. The optimization form already queries backend modules, so the dataset export path should do the same.

Expected benefit:

- Prevents UI/backend mismatch.
- New registered modules become available without frontend edits.
- Removes stale product assumptions.

Tradeoff:

- Dataset export UI must handle module query loading and error states.

## 4. Split `runtime/models/builders.py`

Priority: high.

Affected files:

- `src/fleet_rlm/runtime/models/builders.py`
- `src/fleet_rlm/runtime/models/__init__.py`
- `src/fleet_rlm/runtime/agent/runtime.py`
- tests under `tests/unit/runtime/`

Rationale:

`builders.py` contains too many unrelated runtime responsibilities. It is difficult to reason about recursive workspace behavior, failure handling, memory helpers, and RLM factory behavior in one file.

Expected benefit:

- Easier testing and ownership.
- Lower risk when changing recursive planning or answer synthesis.
- Cleaner extension point for benchmark-specific modules.

Tradeoff:

- Medium refactor with import churn.

Suggested split:

- `runtime/models/rlm_factory.py`
- `runtime/models/memory.py`
- `runtime/models/synthesis.py`
- `runtime/models/workspace/module.py`
- `runtime/models/workspace/planning.py`
- `runtime/models/workspace/verification.py`
- `runtime/models/failure_markers.py`

## 5. Split `runtime/tools/rlm_delegate.py`

Priority: high.

Affected files:

- `src/fleet_rlm/runtime/tools/rlm_delegate.py`
- `src/fleet_rlm/integrations/daytona/bridge_callbacks.py`
- `tests/unit/runtime/agent/test_recursive_workspace.py`
- Daytona integration tests around bridge callbacks and interpreter behavior.

Rationale:

The delegate module handles public tool wrappers, budget leasing, child execution, host workspace snapshot staging, and trace persistence.

Expected benefit:

- Easier reliability review.
- Cleaner tests for child execution and persistence.
- Less risk when modifying snapshot policy or trace logging.

Tradeoff:

- Needs careful preservation of contextvars and bridge callback behavior.

## 6. Make LongCoT Benchmark Reproducible Before Publishing Claims

Priority: high.

Affected files:

- `scripts/run_longcot_eval.py`
- `scripts/generate_longcot_comparison_report.py`
- `scripts/log_benchmark_to_mlflow.py`
- `scripts/setup_longcot_mlflow.py`
- `research/longcot-benchmark-findings.md`
- `research/COMPREHENSIVE_COMPARISON_REPORT.md`
- `docs/explanation/rlm-capability-evaluation.md`

Rationale:

Local artifacts show a promising RLM result, but run provenance is incomplete and some report claims are overstated.

Expected benefit:

- Benchmark claims become reproducible and auditable.
- Per-task traces can be matched to evaluator outcomes.
- Future GEPA work can compare against stable baselines.

Tradeoff:

- Requires more benchmark infrastructure before marketing or release claims.

Minimum required manifest fields:

- Dataset slice path and hash.
- Vendored evaluator commit or version.
- Provider and model config.
- Prompt template or runtime instruction version.
- Runtime config and environment toggles.
- Output JSONL paths and hashes.
- Evaluator output path and hash.
- MLflow run ID and per-task trace IDs.

## 7. Fix LongCoT Formatting Guidance

Priority: high.

Affected files:

- `scripts/run_longcot_eval.py`
- `src/fleet_rlm/runtime/quality/optimize_longcot.py`
- tests for LongCoT answer extraction and formatting.

Rationale:

The RLM runner includes a BlocksWorld-style `solution = [[block, from_stack, to_stack], ...]` reminder even though LongCoT Mini spans logic, cs, chemistry, chess, and math.

Expected benefit:

- Reduces domain-inappropriate answer formatting.
- Improves benchmark comparability.
- Makes formatter behavior explicit per domain.

Tradeoff:

- Needs domain metadata and evaluator contract mapping.

## 8. Add Stratified Dataset Splitting For GEPA

Priority: high.

Affected files:

- `src/fleet_rlm/runtime/quality/datasets.py`
- `src/fleet_rlm/runtime/quality/optimization_runner.py`
- `scripts/generate_longcot_gepa_dataset.py`
- `tests/unit/runtime/quality/test_longcot_dataset.py`
- `tests/unit/runtime/quality/test_optimization_runner.py`

Rationale:

Prefix splitting can create domain-biased validation when generated datasets are ordered by domain.

Expected benefit:

- More meaningful GEPA validation.
- Clearer domain/difficulty reporting.
- Better benchmark alignment.

Tradeoff:

- Existing datasets may need deterministic fallback behavior.

Recommendation:

- Add optional `split` field during dataset generation.
- Otherwise perform deterministic seeded stratification by `domain` and `difficulty` when present.

## 9. Treat LongCoT GEPA Metric As A Surrogate Metric

Priority: high.

Affected files:

- `src/fleet_rlm/runtime/quality/optimize_longcot.py`
- `scripts/run_longcot_eval.py`
- `scripts/generate_longcot_comparison_report.py`
- `docs/how-to-guides/dspy-optimization-and-evaluation.md`

Rationale:

The GEPA metric scores answer similarity and reasoning style. It is not the same as the vendored LongCoT evaluator.

Expected benefit:

- Prevents false claims that GEPA improved official benchmark accuracy.
- Encourages post-optimization evaluation through the actual benchmark evaluator.

Tradeoff:

- Requires extra benchmark runs after optimization.

## 10. Narrow Optimization Dataset Resolution Root

Priority: medium.

Affected files:

- `src/fleet_rlm/api/routers/optimization/_deps.py`
- `docs/how-to-guides/dspy-optimization-and-evaluation.md`
- API route tests.

Rationale:

The default `OPTIMIZATION_DATA_ROOT` is the current working directory, and `_find_dataset_under_root()` can recursively scan the repository.

Expected benefit:

- Faster path resolution.
- Less surprising file discovery.
- Reduced accidental access to artifact or vendor directories.

Tradeoff:

- Users relying on repo-root dataset discovery may need to move files or set an env var.

Recommendation:

- Default to `.data/optimization-datasets` or require explicit `FLEET_RLM_OPTIMIZATION_DATA_ROOT` for path-based datasets.

## 11. Correct Supported Surface Documentation

Priority: medium.

Affected files:

- `README.md`
- `AGENTS.md`
- `.github/copilot-instructions.md`
- `src/frontend/AGENTS.md`
- relevant docs under `docs/`

Rationale:

Supported surfaces are documented inconsistently. History is present in frontend routing and frontend instructions, but omitted in some product-surface lists.

Expected benefit:

- Reduces accidental route regressions.
- Clarifies retired versus supported surfaces.

Tradeoff:

- Documentation-only change unless route policy tests need updates.

## 12. Reconcile Python Version Documentation

Priority: medium.

Affected files:

- `pyproject.toml`
- `AGENTS.md`
- `README.md`
- CI workflow docs if present.

Rationale:

`pyproject.toml` requires Python 3.11+, but repository instructions mention Python 3.10 support.

Expected benefit:

- Prevents setup confusion.
- Keeps release metadata and docs aligned.

Tradeoff:

- If Python 3.10 is intended, dependency and type syntax compatibility need review.

## 13. Update Stale Quality Package Instruction

Priority: low.

Affected files:

- `src/fleet_rlm/AGENTS.md`
- `src/fleet_rlm/runtime/quality/__init__.py`

Rationale:

Instructions say `runtime/quality/__init__.py` imports heavy modules at top level, but current code uses lazy imports.

Expected benefit:

- Future contributors do not preserve outdated assumptions.

Tradeoff:

- Documentation-only.

## 14. Make Module Output Key Explicit In Persistence

Priority: medium.

Affected files:

- `src/fleet_rlm/runtime/quality/module_registry.py`
- `src/fleet_rlm/integrations/database/repository_optimization.py`
- `src/fleet_rlm/integrations/database/models_optimization.py`
- `src/fleet_rlm/integrations/local_store.py`
- migrations under `migrations/versions/`

Rationale:

Repository persistence currently derives output key from the last required key. That works for `question`/`answer`, but it is not a safe general rule.

Expected benefit:

- More robust module metadata.
- Less surprising behavior for future GEPA modules.

Tradeoff:

- Requires schema or migration work if persisted model metadata changes.

## 15. Add A Shared Failure Classification Helper

Priority: medium.

Affected files:

- `src/fleet_rlm/runtime/models/builders.py`
- `src/fleet_rlm/runtime/tools/rlm_delegate.py`
- `scripts/run_longcot_eval.py`
- tests around recursive execution and benchmark extraction.

Rationale:

Failure markers and answer extraction logic appear in multiple places.

Expected benefit:

- More consistent handling of child failures, no-answer results, and benchmark output parsing.
- Fewer subtle differences between runtime and benchmark behavior.

Tradeoff:

- Needs careful regression tests so existing runtime behavior remains stable.

## 16. Move Daytona Evidence Sink Injection Out Of Runtime Internals

Priority: medium.

Affected files:

- `src/fleet_rlm/runtime/agent/runtime.py`
- `src/fleet_rlm/runtime/factory.py`
- `src/fleet_rlm/integrations/daytona/evidence_bridge.py`
- `src/fleet_rlm/runtime/models/evidence.py`

Rationale:

Runtime code should depend on `EvidenceSink` protocol and receive concrete sinks through construction. `AgentRuntime` currently imports `DaytonaEvidenceSink` inside a runtime method.

Expected benefit:

- Cleaner layering.
- Easier alternative evidence sinks in tests or future providers.

Tradeoff:

- Constructor or factory signatures may need adjustment.

## 17. Productize LongCoT Tests Or Keep Them Clearly Local

Priority: medium.

Affected files:

- `tests/unit/test_run_longcot_eval.py`
- `scripts/run_longcot_eval.py`
- `vendor/longcot/`
- CI configuration if tests become tracked.

Rationale:

The test references vendored LongCoT config paths. If the vendor directory is not guaranteed in fresh checkouts, the test can be brittle.

Expected benefit:

- CI stays reliable.
- Local benchmark tests remain useful without hidden dependencies.

Tradeoff:

- May require fixtures or conditional skips.

## 18. Add Domain-Level GEPA And Benchmark Reporting

Priority: medium.

Affected files:

- `src/fleet_rlm/runtime/quality/optimization_runner.py`
- `src/fleet_rlm/runtime/quality/artifacts.py`
- `scripts/generate_longcot_comparison_report.py`
- frontend run details if exposed.

Rationale:

Aggregate accuracy hides severe domain differences, such as the observed chemistry regression.

Expected benefit:

- Better optimization debugging.
- Easier decision-making about whether a prompt improves the target task distribution.

Tradeoff:

- More artifact fields and UI presentation complexity.
