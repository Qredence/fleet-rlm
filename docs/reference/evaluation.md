# Evaluation

Evaluation scorers live in `src/fleet_rlm/optimize/scorers.py`. This page describes
the explicit evaluation workflows. `fleet eval` exposes dataset/run/label commands;
GEPA remains a deliberate isolated optimization workflow.

The [P5.5 runbook](../runbooks/p55-acceptance.md) describes scorer publication,
structured gold, reviewed UC datasets, calibration, spend admission and resumable
paired validation. Registration does not start automatic monitoring.

## Run evaluation against its own server

Use `uv run --env-file .env` for the server, evaluation commands, and trace
verification so Databricks SDK authentication can read the credentials. Fleet's
typed settings read `.env` themselves, but do not export its values into the
process environment used by SDKs.

For the configured `openai:/` Gateway judge, `fleet eval run` applies both the
resolved Gateway API base and credential to its own process, overriding stale
ambient `OPENAI_API_BASE`/`OPENAI_API_KEY` values. Keep those settings paired;
an old provider URL must not receive a credential intended for Databricks.
With duplicate keys in `.env`, use the runbook's python-dotenv wrapper to
preserve FleetSettings' resolution order without editing the file.

The server creates each Turn trace. Selecting an evaluation experiment in the
evaluation client does not redirect another process's runtime trace. With
MLflow 3.17.1, use a separate server with a fixed evaluation experiment and UC
table prefix; preserve the normal server's configuration and concurrency.
See the [evaluation canary runbook](../runbooks/evaluation-canary.md) for the
interactive and automated commands, cost gate, and recovery procedure.

`fleet eval run` creates a unique Session namespace for each invocation. It
prints Session and trace IDs before labeling or scoring. The case ID remains
the idempotency key within that Session. A missing trace, failed Turn,
destination mismatch, unreadable expectations, or scoring error fails the
command instead of reporting successful collection. Trace readback has a
180-second wait budget, including blocked SDK calls; abandoning a blocked read
does not cancel any SQL statement already submitted by the SDK. Scores that
indicate a wrong answer remain evaluation results, distinct from execution or
scorer errors.

After a completed Turn, retry labeling, readback, or scoring using its saved
trace ID. Starting another `fleet eval run` starts fresh execution and is not a
recovery command. `score_evaluation_traces` scores already verified, labeled
traces without sending another Turn.

## Scorers

`build_mlflow_scorer_suite(judge_model, judge_lm=None)` returns:

- MLflow judges: `RelevanceToQuery`, `RetrievalGroundedness`, and two
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
- `fleet_metric` (only when `judge_lm` is given): the same metric GEPA optimizes,
  logged as `fleet_metric` plus one `fleet_metric.<objective>` entry per objective.

Retrieval groundedness is applied only to traces containing a `RETRIEVER`
span. Inapplicable custom scorers return `None`, which MLflow skips, instead of
logging null feedback without an error. Unity Catalog's JSON-wrapped inputs
and outputs are decoded before deterministic, route, and Fleet metric scoring.
Current scoring uses `fleet-metric-v5`; changed definitions invalidate earlier
feedback and candidate approval evidence.

## Metrics

Native DSPy GEPA tunes the Turn program (`AdaptiveFleetEngine`) end to end,
including predictor instructions and generated Flex code through DSPy's built-in
code proposer. It uses the current deployment settings, including persisted
Session overrides. The manifest records effective model configuration for
provenance; replay uses the current deployment configuration and credentials.
Its components are:

- `triage` (`TurnTriage`): route choice
- `direct_cot` (`DirectAnswer`): the no-code answer
- `primary_rlm.generate_action` and `primary_rlm.extract` (`FleetTask`): the RLM loop
- DSPy predictor instructions and generated Flex source.

Every metric scores the Turn's final prediction (`answer`, `route`, `lm_calls`,
`sandbox_errors`) against the example. Each one applies only when its input exists:

| Objective | Applies when | Score |
|---|---|---|
| `answer_correctness` | always | `FleetJudge` verdict: `AnswerJudge` against the gold answer, or `TurnJudge` reference-free when there is none |
| `expected_values` | `expected_values` labelled | 1 if every value appears in the answer |
| `route` | gold `route` labelled | 1 if the Turn took that route |
| `artifact_correctness` | structured artifact gold | 1 if content and required Daytona verification match |
| `delegation_correctness` | structured child gold | 1 if child count, inputs, success and promoted hashes match |
| `efficiency` | always | Measured execution efficiency; it contributes only after all applicable correctness checks pass |

`fleet_metric(gold, pred, trace=None, pred_name=None, pred_trace=None, *, judge_lm)`
returns `dspy.Prediction(score, feedback, objective_scores)`. `quality` is the
minimum of judge correctness/safety, labeled values and route, and applicable
artifact/delegation checks. `score` is zero if quality is below 1; otherwise it is
`0.9 + 0.1 * efficiency`. The judge must report both correctness and safety.
An empty answer scores zero without calling the judge. The feedback lists each
check and its reason, adds the expected answer, and names the component GEPA is
tuning during reflection.

GEPA splits data by source group and searches on train/dev. Holdout is excluded
from search and evaluated only afterward. Each candidate and the incumbent run
three paired validation passes. A critical case passes only when its quality is
1 in every pass; composite score 1 is not the critical-case rule. Promotion
requires no aggregate quality regression on dev or holdout, improved dev
composite score, no holdout composite regression, zero holdout execution errors,
and measured usage. Efficiency distinguishes fully correct programs, but cannot
compensate for an incorrect answer.

Approval rechecks that the current incumbent, configuration, code, and
dependencies match the candidate manifest. A mismatch requires reevaluation;
old manifests are not an unchecked rollback path.

## Explicit historical trace evaluation

Compiling or optimizing does not automatically score historical traces. The
separate `auto_evaluate_traces` operation remains available for an explicit
evaluation run. It records scorer version, judge route, and an expectations
fingerprint; matching assessments can be reused and stale ones rescored.

## DSPy integration rationale and validation

The integration follows the locked DSPy 3.4.1 contracts. Native GEPA treats each
Flex as a whole source-code component; predictors inside that Flex are owned by
the source and are not separately tuned. Ordinary predictors remain instruction
components. Fleet supplies the metric and isolated runtime, rather than a custom
search loop or code proposer. See the [versioned Flex contract](https://github.com/stanfordnlp/dspy/blob/3.4.1/docs/docs/api/modules/Flex.md)
and [GEPA implementation](https://github.com/stanfordnlp/dspy/blob/3.4.1/dspy/teleprompt/gepa/gepa.py).

LM and adapter overrides use scoped `dspy.context` calls. DSPy's evaluator owns
worker context propagation. JSON state preserves optimized instructions and Flex
source; the host reconstructs the Daytona interpreter and current credentials.
See [settings and context](https://github.com/stanfordnlp/dspy/blob/main/docs/docs/diving-deeper/settings-and-context.md)
and [saving and loading](https://github.com/stanfordnlp/dspy/blob/main/docs/docs/diving-deeper/saving-and-loading.md).

`tests/optimize/test_native_gepa.py` exercises actual native compilation with
scripted models and a fake interpreter that rejects unknown source. For explicitly
authorized live validation, `uv run python scripts/verify_gepa_live.py --live`
uses synthetic addition tasks, the configured Gateway models and judge, and
disposable Daytona runtimes. Its deliberately incorrect seed checks source
rewriting, held-out execution, and JSON reload; it does not establish production
quality improvement or approve an artifact. Reports are local under
`.scratch/gepa-validation/`.

## Judges

MLflow scorers use `FLEET_JUDGE_MODEL`. `fleet_metric` uses `FleetJudge`, a
`dspy.Module`, on its DSPy twin: `FleetSettings.build_judge_lm`, configured through
the gateway. Judge nondeterminism is possible and must be measured. GEPA reflection
uses `FLEET_REFLECTION_MODEL`; the checked-in configuration currently uses Claude
Haiku 5.5 at temperature 1.0 with a 32,000-token output budget.

## Trace contents and selected replay evidence

The `fleet_turn` root span records `session_id`, `turn_id`, task, workspace
manifest, file digests, whether replay capture was selected, and the snapshot
digest. Capture is off by default. A selected Turn stores an immutable snapshot
in `fleet_lite_replay_snapshots` in the same transaction as successful Turn
completion. It contains bounded exact workspace file bytes, conversation history,
memory, selected dataset handle, Skills catalog and revision, execution mode,
non-secret configuration, timeout, and Fleet version. Total workspace input is
limited to 8 MB; oversized
selected Turns fail before model execution. Snapshot bytes remain in Lakebase;
the GEPA dataset carries only the Session/Turn reference and digest.

GEPA accepts only approved gold-labeled traces whose selected snapshot exists,
passes its integrity checks, and matches the trace task, manifest, and file
digests. Unselected, missing, changed, or malformed snapshots are excluded. The
isolated rollout restores the captured file bytes and Skills revision rather
than reading mutable current VFS or local Skills. The `compiled_program_digest`
attribute names the approved program that served the Turn (empty when none is
installed). Selected replay snapshots expire after 30 days and are pruned when
the Store initializes; their Lakebase curation rows are deleted with them. Do
not enable capture for data that is not approved for this use.
`fleet eval label` writes MLflow assessments and, for a selected replay, copies
the review status, source group, answer/expected values, route, critical marker,
reviewer, and matching replay provenance into `fleet_lite_review_labels` in
Lakebase. This table is the PostgreSQL curation source; its foreign key follows
replay deletion, and the dataset loader rechecks the saved task, manifest, and
file digests against the immutable snapshot. Older or unselected traces can
still be labeled for MLflow evaluation, but they are not eligible for GEPA.
Isolated GEPA rollouts restore the immutable selected task, workspace bytes,
conversation, memory, dataset handle, and Skills revision. They use current
deployment settings and credentials; recorded model configuration is provenance
only. Credentials are never captured in the snapshot. A replay captured under an
unsupported sandbox transport is rejected. Lossy MLflow traces carry identity
references only and do not supply replay content.

Framework-captured inputs and outputs pass through a recursive span processor that
redacts credential-like fields and bounds strings, collections, and nesting before
export. This is a defense against accidental credential disclosure, not a complete
PII detector; verify with synthetic canaries before live Databricks trace export.

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

Benchmark cases carry `review_status=benchmark` and are excluded from GEPA's curated train/dev/holdout dataset;
run them separately with `fleet eval run`. GEPA must not train or validate on these benchmark traces.
`expected_values_present` matches on word boundaries, so `12` does not match `120`. It is a containment check,
not the official benchmark scoring.

Critical deterministic cases are explicitly labeled by a reviewer with `fleet eval label ... --critical` and one or
more `--value` expectations. Their complete source groups are pinned to holdout, and promotion requires every such
case to have quality 1.0 on every pass. Candidate approval fails closed when the holdout has no critical case.

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

The manual 30-day Unity Catalog trace cleanup procedure is documented in the
[MLflow trace retention runbook](../runbooks/mlflow-trace-retention.md).

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
retain provider-controlled settings. ReAnchor remains deferred; native GEPA integration is covered by the offline and live canaries.
