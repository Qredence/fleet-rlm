# Benchmark Analysis

## Scope

This report focuses on benchmark-related work in the current branch, especially LongCoT Mini. It reviews the benchmark code, local artifacts, result claims, reproducibility, and relationship to the base architecture.

Important files and artifacts:

- `scripts/generate_longcot_gepa_dataset.py`
- `scripts/generate_longcot_comparison_report.py`
- `scripts/run_longcot_eval.py`
- `scripts/log_benchmark_to_mlflow.py`
- `scripts/setup_longcot_mlflow.py`
- `scripts/benchmarks/longcot_mini_stratified_100.json`
- `scripts/benchmarks/longcot_mini_missing_21.json`
- `scripts/benchmarks/longcot_mini_rlm_missing_55.json`
- `scripts/benchmarks/longcot_mini_stratified_100_remaining.json`
- `scripts/benchmarks/longcot_mini_stratified_100_remaining_v2.json`
- `output/longcot-eval/final/direct_100_tasks.jsonl`
- `output/longcot-eval/final/rlm_100_tasks.jsonl`
- `output/longcot-eval/final/direct_eval.json`
- `output/longcot-eval/final/rlm_eval.json`
- `output/longcot-eval/archive/longcot-rlm-transport-summary.json`
- `output/longcot-eval/longcot_gepa_dataset.jsonl`
- `research/longcot-benchmark-findings.md`
- `research/COMPREHENSIVE_COMPARISON_REPORT.md`
- `mlflow_verification_report.md`
- `tests/unit/test_run_longcot_eval.py`

## What The Current Branch Adds Or Changes

Committed branch changes include:

- LongCoT GEPA dataset generation in `scripts/generate_longcot_gepa_dataset.py`.
- LongCoT comparison report generation in `scripts/generate_longcot_comparison_report.py`.
- A new LongCoT optimization module in `src/fleet_rlm/runtime/quality/optimize_longcot.py`.
- Registry integration in `src/fleet_rlm/runtime/quality/module_registry.py`.
- GEPA runner changes in `src/fleet_rlm/runtime/quality/optimization_runner.py` and `src/fleet_rlm/runtime/quality/gepa_optimization.py`.
- Optimization API and persistence changes in `src/fleet_rlm/api/routers/optimization/`, `src/fleet_rlm/integrations/database/repository_optimization.py`, and `src/fleet_rlm/integrations/local_store.py`.
- Tests for LongCoT dataset generation, module registry, GEPA runner behavior, API behavior, and persistence.

Uncommitted or untracked benchmark files in the working tree include:

- `scripts/run_longcot_eval.py`
- `scripts/log_benchmark_to_mlflow.py`
- `scripts/setup_longcot_mlflow.py`
- `tests/unit/test_run_longcot_eval.py`
- local JSON benchmark slices under `scripts/benchmarks/`
- local result artifacts under `output/longcot-eval/`
- local research reports under `research/`
- `mlflow_verification_report.md`

These files are important to the current branch analysis, but they are not yet a polished, reproducible benchmark workflow.

## Benchmark Structure

The LongCoT Mini benchmark compares two modes:

1. Direct LongCoT inference through vendored benchmark scripts and provider config.
2. RLM inference through `fleet-rlm` recursive workspace runtime components.

Direct mode:

- Uses `vendor/longcot/src/run_inference.py` and `vendor/longcot/src/run_eval.py` from the local vendored LongCoT checkout.
- Uses config such as `vendor/longcot/src/configs/or_deepseek_v4_flash.yaml`.
- Produces direct JSONL output with fields such as `attempts`, `reasoning`, `response_text`, `usage`, `successful`, `domain`, `difficulty`, and `question_id`.

RLM mode:

- Uses `RecursiveWorkspaceModule` from `src/fleet_rlm/runtime/models/builders.py` rather than the full WebSocket workbench path.
- Constructs or reuses Daytona interpreter state through thread-local runtime setup.
- Uses a local evidence sink in the benchmark script.
- Produces JSONL output with fields such as `runtime_status`, `transport_status`, `mlflow_trace_id`, `mlflow_client_request_id`, `contract_warning`, `elapsed_ms`, `response_text`, `domain`, `difficulty`, and `question_id`.

Evaluation:

- Final direct and RLM outputs are evaluated by the vendored LongCoT evaluator.
- GEPA's LongCoT module uses a separate local metric in `src/fleet_rlm/runtime/quality/optimize_longcot.py`, not the vendored benchmark evaluator.

## LongCoT Mini Dataset

The main declared benchmark slice is `scripts/benchmarks/longcot_mini_stratified_100.json`.

Observed characteristics:

- Name: `longcot-mini-stratified-100`.
- Seed: `42`.
- Total tasks: `100`.
- Domains: 20 each for `logic`, `cs`, `chemistry`, `chess`, and `math`.

This is a reasonable exploratory slice because it is balanced across domains and small enough for repeated runs.

However, the GEPA dataset generated from LongCoT answers is not the same as the 100-task benchmark slice:

- `output/longcot-eval/longcot_gepa_dataset.jsonl` has 80 rows.
- It includes 20 each for `cs`, `chemistry`, `chess`, and `math`.
- It excludes `logic`, because the generator skips examples without usable answer fields.

This is highly relevant because the final RLM benchmark result is strongest on `logic`. GEPA optimization against a dataset that excludes `logic` should not be assumed to improve the benchmark's strongest domain.

## Observed Final Results

The final local evaluation artifacts report:

Direct baseline in `output/longcot-eval/final/direct_eval.json`:

- Total: 100
- Correct: 13
- Incorrect: 56
- Failed: 31
- Wrong formatting: 13
- Accuracy: 0.1884 when failed examples are excluded
- Overall accuracy: 0.13 across all examples

RLM in `output/longcot-eval/final/rlm_eval.json`:

- Total: 100
- Correct: 33
- Incorrect: 67
- Failed: 0
- Wrong formatting: 67
- Accuracy: 0.33
- Overall accuracy: 0.33

Domain breakdown:

| Domain | Direct Correct / Total | RLM Correct / Total | Main Observation |
| --- | ---: | ---: | --- |
| chemistry | 6 / 20 | 0 / 20 | RLM regresses sharply. |
| chess | 2 / 20 | 8 / 20 | RLM improves. |
| cs | 1 / 20 | 7 / 20 | RLM improves. |
| logic | 2 / 20 | 16 / 20 | RLM improves strongly. |
| math | 2 / 20 | 2 / 20 | No accuracy gain. |

The headline improvement from 13 percent overall accuracy to 33 percent overall accuracy is meaningful as a local artifact, especially because RLM eliminates evaluation failures. But it is not uniformly positive across domains.

The chemistry regression is severe and should be treated as a major finding, not a footnote.

## What Can Be Concluded

The current artifacts support these cautious conclusions:

1. On the observed 100-task local slice, the RLM output file evaluated better overall than the direct output file.
2. RLM eliminated the direct mode's 31 failed evaluations in the final artifacts.
3. RLM performed much better on `logic`, `cs`, and `chess` in this slice.
4. RLM did not improve `math` accuracy.
5. RLM performed worse than direct mode on `chemistry`.
6. Formatting remains a major problem for RLM outputs, even when answers are marked correct.

These are useful engineering findings. They can guide prompt, parser, and answer-normalization work.

## What Cannot Be Concluded Yet

The current artifacts do not support these stronger claims without more evidence:

1. That RLM is generally superior to direct LongCoT across the full benchmark.
2. That the result is reproducible across fresh runs, providers, or model configs.
3. That GEPA improved LongCoT benchmark performance.
4. That incorrect RLM answers are exclusively formatting failures.
5. That the reported result is statistically significant unless the exact statistical test and paired outcome data are included.
6. That the transport path succeeded for all 100 tasks, because the archived transport summary covers only 55 tasks.

## Methodology Concerns

### RLM Transport Summary Covers 55 Tasks, Not 100

Evidence:

- `output/longcot-eval/archive/longcot-rlm-transport-summary.json`

The archived transport summary reports 55 tasks, all successful. It covers:

- `cs`: 1
- `chemistry`: 14
- `chess`: 20
- `math`: 20

It does not cover the full 100-task final benchmark slice and does not include `logic`. Reports should avoid implying that this summary proves 100/100 transport success.

### Formatting Claims Are Overstated In Research Drafts

Evidence:

- `research/longcot-benchmark-findings.md`
- `research/COMPREHENSIVE_COMPARISON_REPORT.md`
- `output/longcot-eval/final/rlm_eval.json`

The research drafts characterize RLM failures as formatting-driven. The final evaluator details are more nuanced:

- 54 of 67 RLM incorrect examples have `wrong_formatting = true`.
- 13 RLM incorrect examples do not have `wrong_formatting = true`.
- 13 RLM correct examples also have `wrong_formatting = true`.

Therefore, `wrong_formatting` is not equivalent to incorrectness. It overlaps with both correct and incorrect statuses.

### Statistical Significance Claim Needs Provenance

Evidence:

- `research/COMPREHENSIVE_COMPARISON_REPORT.md`

The comprehensive report mentions statistical significance. The repository evidence reviewed here did not include the statistical test implementation, confidence interval calculation, or paired per-question significance analysis.

Because direct and RLM outputs are paired by question ID, any significance claim should use a paired test or bootstrap over paired outcomes, not only independent aggregate percentages.

### LongCoT Runner Uses A BlocksWorld-Specific Format Reminder

Evidence:

- `_LONGCOT_FORMAT_REMINDER` in `scripts/run_longcot_eval.py`

The RLM runner contains a format reminder that asks for BlocksWorld-style moves such as `solution = [[block, from_stack, to_stack], ...]`. LongCoT Mini includes `logic`, `cs`, `chemistry`, `chess`, and `math` tasks.

A domain-specific output reminder can improve one class of task while harming others. It may contribute to wrong-formatting behavior on non-BlocksWorld domains. The benchmark prompt should be domain-aware or evaluator-contract-aware rather than hardcoded to one task family.

### Direct And RLM Modes Are Not Symmetric

Direct mode and RLM mode use different execution paths:

- Direct mode uses vendored LongCoT inference scripts.
- RLM mode uses `RecursiveWorkspaceModule` and Daytona-backed execution.

This is expected because the benchmark compares architectures, but it means comparability depends on careful controls:

- Same model or explicitly documented model difference.
- Same provider config or explicitly documented provider difference.
- Same maximum token budget or documented budget difference.
- Same evaluation script and exact evaluator version.
- Same retry/failure policy.
- Same task order and task IDs.
- Same answer extraction rules where possible.

The local artifacts contain useful evidence, but not all of this provenance is captured in one durable manifest.

### MLflow Trace Linkage Is Incomplete

Evidence:

- `mlflow_verification_report.md`

The MLflow verification report states:

- The RLM pilot run has zero individual task traces linked to it.
- Later traces exist, but only 20 of 557 traces have key benchmark metadata such as `mode`, `model`, `difficulty`, and `question_id`.
- `mlflow.sourceRun` is null for every trace.

This weakens reproducibility and debugging. A benchmark result should link each task output, evaluator decision, trace, model config, and run artifact together.

### Local Scripts Contain Hardcoded Or Machine-Specific Paths

Evidence:

- `scripts/setup_longcot_mlflow.py`
- `scripts/log_benchmark_to_mlflow.py`
- `vendor/longcot/`
- `mlartifacts/4/`

Some scripts reference local artifact IDs, local experiment assumptions, or vendored checkout paths. That is acceptable for an exploratory branch, but it is not yet a repeatable project workflow.

## GEPA Dataset Concerns For LongCoT Mini

Evidence:

- `scripts/generate_longcot_gepa_dataset.py`
- `output/longcot-eval/longcot_gepa_dataset.jsonl`
- `src/fleet_rlm/runtime/quality/datasets.py`

The GEPA dataset generator loads LongCoT source tasks and writes JSONL examples with:

- `question_id`
- `domain`
- `difficulty`
- `question`
- `answer`

It skips examples without answers. In the observed output, this removes the `logic` domain.

The optimization runner then uses a prefix train/validation split. If rows remain grouped by domain, the validation split can be mostly or entirely a single domain. For the observed 80-row output and a typical 80/20 split, validation is likely dominated by the tail domain rather than representing all domains.

This is not a sound validation strategy for a stratified benchmark.

Recommendations:

- Preserve domain-balanced splits in the dataset manifest.
- Add `split` fields at generation time.
- Use deterministic seeded shuffling or stratified train/validation assignment.
- Include domain and difficulty breakdown in GEPA evaluation artifacts.
- Run the official LongCoT evaluator on held-out optimized outputs before making benchmark claims.

## Benchmark Result Interpretation

The direct baseline failed 31 tasks, while RLM failed 0 tasks in the final evaluator artifacts. This is important because the direct `accuracy` metric excludes failed tasks, while `overall_accuracy` includes them.

Both should be reported:

- Direct accuracy excluding failures: 18.84 percent.
- Direct overall accuracy: 13 percent.
- RLM accuracy and overall accuracy: 33 percent.

The most honest summary is:

> On this local 100-task LongCoT Mini slice, RLM produced more evaluator-correct answers overall and avoided execution/evaluation failures, but it regressed badly on chemistry, did not improve math, and still had substantial formatting issues. The current artifacts are promising but not yet sufficient for a reproducible benchmark claim or GEPA-improvement claim.

## Impact Compared With Base Architecture

The benchmark work exercises and extends these base architecture areas:

- Recursive workspace runtime in `src/fleet_rlm/runtime/models/builders.py`.
- Daytona interpreter and evidence handling.
- GEPA optimization registry and runner.
- MLflow observability and artifact logging.
- Optimization API and UI surfaces.

The positive impact is that benchmarks are being integrated with real product runtime concepts rather than isolated toy scripts. That is the right direction.

The negative impact is that benchmark-specific behavior is currently spread across local scripts, research markdown, output directories, quality modules, API routes, and MLflow setup scripts. Without cleanup, benchmark logic will become difficult to reproduce and harder to separate from production optimization behavior.

## Recommended Benchmark Stabilization Plan

Priority: high.

Affected files:

- `scripts/run_longcot_eval.py`
- `scripts/generate_longcot_gepa_dataset.py`
- `scripts/generate_longcot_comparison_report.py`
- `scripts/log_benchmark_to_mlflow.py`
- `tests/unit/test_run_longcot_eval.py`
- `docs/explanation/rlm-capability-evaluation.md`
- `docs/how-to-guides/dspy-optimization-and-evaluation.md`

Recommended steps:

1. Promote the LongCoT runner from local script to documented benchmark command only after removing local path assumptions.
2. Add a benchmark manifest that records dataset slice, model config, provider, evaluator commit, runtime config, prompt version, output paths, and MLflow run IDs.
3. Make RLM output formatting domain-aware.
4. Add per-task trace linkage and ensure `mlflow.sourceRun` or equivalent run association exists.
5. Replace broad formatting claims with evaluator-derived confusion tables.
6. Include paired statistical analysis if statistical significance is claimed.
7. Keep GEPA optimization results separate from benchmark comparison results until optimized modules are evaluated through the same LongCoT evaluator.
