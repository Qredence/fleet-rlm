# External task benchmarks

Use the [LongCoT](../../notebooks/longcot_benchmark.ipynb) or
[OOLONG-Synth](../../notebooks/oolong_benchmark.ipynb) notebook:

```bash
uv sync --locked --group benchmarks
```

## Prepare, execute, score

1. Verify the pinned dataset revision and deterministic selection. Keep the
   immutable input/gold bundle in a new directory; gold never enters Fleet requests.
2. Obtain live-service authorization and a budget, then set `ALLOW_LIVE=True`.
   Run fresh Sessions against the notebook's checked tracking destination.
3. Retain submission receipts, durable outcomes, trace IDs, configuration digests,
   usage and cleanup results. Stop on uncertain execution or unresolved cleanup.
4. Score saved answers with the official verifier. Set `ALLOW_JUDGES=True`
   separately for paid Fleet judging; it does not rerun tasks.

Rerunning a finished cell reuses completed receipts. Failed/uncertain attempts
are never replaced. Inspect an interrupted `.running` marker and its Sessions
before clearing it. Changing selection/protocol requires a new bundle/run.

Report selected/completed/scored counts, official scores, exact correctness,
latency and usage separately. Include failures and unattempted tasks in
all-selected-case accuracy. Missing usage/finish reasons remain unknown.
Benchmark labels and `fleet.benchmark.*` tags exclude traces from GEPA curation.

## LongCoT comparison pilot

The notebook's default pilot pairs one native DSPy direct call with strict RLM
for five easy tasks, one per domain (CS, chemistry, chess, math, logic). It permits
ten serial attempts, alternating arm order. Set `HF_PIN_VERIFIED` only after
checking the pinned source. The first pair must have valid traces, strict routing,
released Sessions and official scores before continuing.

Use fresh tracking storage. Disable shared learned notes and root/sub caches;
set the sub model to the root model. Preserve role output caps and Fleet budgets.
The notebook checks service/model identity. It uses `timeout_seconds=0`; native
transport and provider bounds still apply. Keep exact requests, raw SSE, actual
routes, model/source identities and cleanup receipts. A wrong answer is an
observation, not permission to tune or retry.

`score_comparison_pilot` uses the pinned verifier with model fallbacks disabled.
`ALLOW_OFFICIAL_EVALUATION=True` logs deterministic MLflow evaluation without a
judge. `ALLOW_JUDGES` does not enable paid judges in comparison mode. Five pairs
are diagnostic and cannot establish statistical improvement.

## Runtime and generation comparisons

With separate live authorization, capture before/after runtime reports:

```bash
uv run python scripts/verify_runtime_live.py --live --output baseline.json
uv run python scripts/verify_runtime_live.py --live --output after.json
uv run python scripts/compare_runtime_reports.py --baseline baseline.json --after after.json
```

The comparator checks routes, outcomes, stream shape, budget units and latency.
For root/sub token-cap comparisons, use
`scripts/compare_generation_latency.py plan|collect|compare` (`--help` lists flags).
Its fixed protocol uses three arms and 39 Turns. Preserve controls and saved
receipts; recover completed work rather than collecting replacements. Acceptance
requires correctness, no truncation, no aggregate call increase, a passing Pi
canary and at least 10% lower median short-task latency. Missing identity or
finish-reason evidence blocks acceptance. The tool never changes serving defaults.

Shared tooling: [benchmark_tasks.py](../../scripts/benchmark_tasks.py) and
[oolong_scoring.py](../../scripts/oolong_scoring.py). Fleet's own metric and
candidate gates are in [evaluation](../reference/evaluation.md).
