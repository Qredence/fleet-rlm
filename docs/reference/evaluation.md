# Evaluation

Evaluation scorers live in `src/fleet_rlm/memory_and_eval.py`. This page describes
what exists today; dataset building, `mlflow.genai.evaluate` runs, and GEPA
entry points are not yet wired into the CLI.

## Scorers

`build_mlflow_scorer_suite(judge_model)` returns:

- MLflow judges: `RelevanceToQuery`, `RetrievalGroundedness`, `Safety`, and two
  `Guidelines` rubrics (sandbox verification, Skill progressive disclosure).
- `rlm_trajectory_efficiency`: penalizes model calls beyond three and sandbox
  tracebacks. Model calls are counted by `SpanType.LLM`, not span names.
- `jev_routing_efficiency`: rewards fast paths without RLM/Flex and bounded
  RLM/Flex runs.
- `route_correctness`: compares the turn's reported `route` (`direct_cot`,
  `flex_pipeline`, `deep_rlm`) with the `route` expectation on a labeled case.
  It returns no value when a case has no gold route.

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
