# Evaluation

Evaluation scorers live in `src/fleet_rlm/optimize/scorers.py`. This page describes
the explicit evaluation workflows. `fleet eval` exposes dataset/run/label commands;
GEPA remains a deliberate isolated optimization workflow.

## Scorers

`build_mlflow_scorer_suite(judge_model)` returns:

- MLflow judges: `RelevanceToQuery`, `RetrievalGroundedness`, `Safety`, and two
  `Guidelines` rubrics (sandbox verification, Skill progressive disclosure).
- `rlm_trajectory_efficiency`: penalizes model calls beyond three and sandbox
  tracebacks. Model calls are counted by `SpanType.LLM`, not span names.
- `jev_routing_efficiency`: rewards fast paths without RLM/Flex and bounded
  RLM/Flex runs.
- `route_correctness`: compares the turn's reported `route` (`direct`, `flex`,
  `rlm`) with the `route` expectation on a labeled case. Labels and traces
  written with the old names (`direct_cot`, `single_rlm`/`flex_pipeline`,
  `delegated_rlm`/`deep_rlm`) are read as their new equivalents. It returns no
  value when a case has no gold route.

Jev is the TypeSafe routing model (`FLEET_TYPESAFE_MODEL`); it is evaluated as a
routing component, not used as a judge.

## Judges

MLflow scorers use `FLEET_JUDGE_MODEL`. The GEPA metric (`gepa_mlflow_metric`)
uses a DSPy judge on the reflection LM; MLflow judge URIs are never passed as
DSPy model IDs (see `ARCHITECTURE.md`).

## Trace previews

Each `fleet_turn` trace records `request_preview` and `response_preview`
(first 200 characters of the task and answer) for the MLflow trace list.

## RLM-scale cases

- **Synthetic long context** (`--long-context`): whole-file counting, top-k, list, and needle tasks over
  generated logs of about 20K, 50K, 100K, and 200K characters. Gold values are computed from the log.
  `context_chars` and `answer_type` (`scalar` or `list`) are recorded so scores can be sliced by size and shape.
- **Oolong-synth** (`--oolong N`): validation items up to 32,768 tokens of context, strided across the split with
  at most two questions per context window. Fetched from the Hugging Face datasets-server with backoff on 429.
- **LongCoT** (`--longcot N`): items whose gold answer is a scalar or flat list (chemistry, chess, math). The `logic`
  domain ships no gold answer and `cs` answers are structured, so both need the official verifier and are skipped.

Benchmark cases carry `review_status=benchmark` and belong in the held-out split only; GEPA must not train or
validate on them. `expected_values_present` matches on word boundaries, so `12` does not match `120`.
It is a containment check, not the official benchmark scoring.

## Stopping regression evaluation

`fleet_rlm.optimize.evaluation.evaluate_stopping_traces(traces, judge_model=...)` is an
explicit offline evaluation entry point for captured MLflow traces. It combines
answer completion, expected-value presence, and a Guidelines judge over bounded,
chronological task code and observations. Bootstrap code and predictor reasoning
are excluded. Missing or oversized evidence is unavailable, not a passing grade.

The policy is to submit on the next action once the answer and a supporting
check agree. Additional investigation remains appropriate for failed checks,
disagreement, or an explicit independent-verification request. This is guidance
and evaluation, not a host-enforced completion gate. Judge execution uses the
explicit model argument and is never part of the runtime request path.


## Decision regression and configuration comparisons

`tests/fixtures/triage_cases.json` contains 18 developer-reviewed routing cases,
six per effective route. Compare unchanged and structured criteria using the same
backend, inputs and existing `route_correctness` scorer. Preserve probability,
confidence and cache evidence; revised criteria must not regress aggregate accuracy.
Mode overrides and context protection are deterministic invariants.

Batched Jev responses keep `item` and `decision` and add `raw_choice`,
`probabilities`, `confidence`, `backend` and `model`. Routing records both the raw
choice and the effective route/override reason. Missing evidence remains null.
Generative confidence is not calibrated confidence. No thresholds are fitted in
ordinary turns, and no additional runtime judge calls are introduced.

Configuration comparison uses three repetitions each of direct answer,
computation/dataset and explicit parallel tasks, plus the exact Pi task once per
configuration. Keep context and model inputs fixed, capture configuration digests,
and report correctness, calls and latency separately. Root/sub token candidates
16384/8192 are adopted only if the documented acceptance gates pass; otherwise
retain provider-controlled settings. ReAnchor and GEPA adapter changes are deferred.
