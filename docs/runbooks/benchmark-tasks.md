# External task benchmarks

Fleet owns its DSPy metrics, scorers, judges, evaluators, MLflow/Databricks experiments,
and native Flex/GEPA optimization. External benchmark families supply tasks and official
verification through development notebooks; they add no runtime policy or CLI flags.

Use [LongCoT](../../notebooks/longcot_benchmark.ipynb) or
[OOLONG-Synth](../../notebooks/oolong_benchmark.ipynb). Install their optional environment:

```bash
uv sync --locked --group benchmarks
```

Select the repository's `.venv` kernel. Start the normal service with `uv run fleet cli`
or `uv run fleet cli databricks`, and match `MLFLOW_MODE` in the notebook to the service.
The notebook reads normal Fleet settings. API execution preserves the service's mode,
models, retries, token caps, cache, and shared notes configuration. It does not start
services, replace provider engines, or select a custom benchmark TOML.

## Prepare tasks

Preparation uses pinned Hugging Face dataset revisions and deterministic selection.
LongCoT defaults to ten easy tasks across five domains; OOLONG defaults to twelve
validation tasks spanning task groups and context sizes. These are diagnostic samples.
OOLONG projects small metadata columns first, then loads selected unlabelled contexts.
LongCoT joins selected HF IDs to the pinned official package for structured verification,
requiring prompt, domain, and difficulty identity.

The immutable `bundle.json` contains inputs, source metadata, and host-only gold. A
digest detects edits. To change the selection or protocol, choose a new bundle directory.
Existing bundles are loaded without downloading or resampling. Gold, label-enriched
contexts, and verifier payloads are never included in Fleet requests.

## Execute and score

Set `ALLOW_LIVE=True` deliberately to execute tasks in fresh sessions. The helper writes
a receipt before submitting each turn, verifies the notebook tracking destination before
execution, confirms durable outcomes, records effective safe
configuration and digest, collects available provider usage, and releases the session.
It stops on uncertain execution, exhausted provider rate limits, unhealthy services,
configuration changes, or unresolved cleanup. Saved failed/uncertain receipts cannot be
replayed by rerunning a cell. A leftover `.running` file indicates an interrupted or active
runner: inspect its receipts and Fleet sessions before clearing that marker manually.

Re-running a finished run skips its completed receipts. Use a new run directory only for
an intentional new measurement. A stopped run stays incomplete; official scoring reads
saved answers without sending tasks again. Missing usage remains unknown, and reported
token counts do not establish monetary cost.

Official verifier scores, exact correctness, completion/failure counts, completed-case
accuracy, all-selected-case accuracy, latency, usage, configuration digests, and trace IDs
are reported separately. Total and mean latency include attempted tasks, including failures
and preparation, and exclude session release. All-selected-case accuracy counts failures and unattempted cases
in its denominator; it is a lower bound when scoring is incomplete. OOLONG's fractional
official score is also reported separately. Verifier errors remain explicit.

Set `ALLOW_JUDGES=True` separately to apply Fleet's existing MLflow scorer suite and DSPy
judge to the saved traces. This incurs judge usage but never executes the tasks again.
The notebook verifies the recorded tracking destination against its current settings,
uses the configured evaluation experiment, logs the selected task dataset and source
metadata, and records official correctness as an additional benchmark assessment.
Evaluation records selected, completed, and scored case counts and saves `complete: false`
when only part of a stopped run was scored; it never re-executes tasks.
Benchmark traces are labeled `review_status=benchmark`, excluding them from automatic
optimization curation. Missing trace delivery or scoring errors leave recoverable run IDs.

## Tooling and historical evidence

The shared helper is [benchmark_tasks.py](../../scripts/benchmark_tasks.py); the small
attributed [OOLONG scorer](../../scripts/oolong_scoring.py) preserves upstream parsing.
Dataset selection and verifier configuration live in the notebooks. Notebook dependencies
are outside normal runtime/development installation.

Runtime parity is measured by a producer/consumer pair.
[verify_runtime_live.py](../../scripts/verify_runtime_live.py) runs the live acceptance
cases and records a `metrics` entry per case: wall clock, LM calls, route, terminal
outcome, and stream shape. [compare_runtime_reports.py](../../scripts/compare_runtime_reports.py)
compares those entries against one or more baseline reports and exits non-zero when a
case changes its route, outcome, or event shape, spends more budget units, or exceeds a
latency allowance of the larger of 10% or the baseline's own spread:

```bash
uv run python scripts/verify_runtime_live.py --live --output baseline-1.json
uv run python scripts/verify_runtime_live.py --live --output after.json
uv run python scripts/compare_runtime_reports.py \
    --baseline baseline-1.json --after after.json
```

Both scripts require live provider, database, and sandbox authorization; neither runs in
ordinary CI.

Direct-model comparisons, custom quota/admission profiles, benchmark-specific GEPA
policies, and database comparison procedures are retired. Historical reports are kept
in the local `docs/benchmarks/` archive and excluded from Git for now. Saved bundles,
receipts, and MLflow traces remain evidence of their original protocols. New notebook
runs use distinct directories and do not reinterpret them.

Fleet's own [evaluation and optimization](../reference/evaluation.md) remain supported.
The current shared metric is `fleet-metric-v10` and approval policy is version 6; older
candidate evidence requires reevaluation. This cleanup does not migrate stored labels
or install an optimization candidate.
