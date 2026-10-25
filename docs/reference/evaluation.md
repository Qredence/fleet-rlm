# Evaluation

Evaluation scorers live in `src/fleet_rlm/optimize/scorers.py`. This page describes
the explicit evaluation workflows. `fleet eval` exposes dataset/run/label commands;
GEPA remains a deliberate isolated optimization workflow.

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

`build_mlflow_scorer_suite(judge_model, judge_lm=None, jev_lm=None)` returns:

- MLflow judges: `RelevanceToQuery`, `RetrievalGroundedness`, and versioned
  Fleet rubrics for route suitability, execution verification, tool integrity,
  output instruction compliance, and Skill progressive disclosure.
- `jev_routing_efficiency_judge`: judgment of whether the selected route fit
  the task. This is separate from `jev_routing_efficiency`, the deterministic
  budget-efficiency score. When `jev_lm` is given (`FleetSettings.build_jev_lm`,
  a TypeSafe `jev-*` client), one System One request asks two independent
  questions — `Noul` route suitability and a `Choice` over `direct`/`flex`/`rlm`
  — and the verdict is the `Noul` value at threshold 0.5. The rationale is
  derived evidence (`p(route reasonable)=…`, best-fit route, recorded
  `lm_calls`/`sandbox_errors`), not model-written prose; the raw probability,
  confidence, and route distribution are recorded as `fleet.jev.*` metadata.
  Missing task, route, or nonnegative integer usage evidence returns `false`
  without calling Jev. Rubric wording is load-bearing for a System One model:
  state an unconditional direct-route prohibition and Jev rejects trivial direct
  turns, so the file/dataset rule is conditioned on the task instead. Measured on
  2026-10-21 over 45 saved Experiment 1 traces, every Jev verdict matched the
  generative judge; that population contained no failing route, so agreement on
  incorrect routing remains unmeasured. Without `jev_lm` the suite falls back to the generative
  judge and records `fleet.judge_backend=generative`.
- `execution_evidence_diagnostics`: one structured judgment emits both
  `sandbox_verification_discipline` and `tool_execution_integrity`. It receives
  a bounded summary of the root task/answer and actual `sandbox.exec` span IDs,
  lineage, status, outcome, code, stdout, stderr, and return values (including
  `SUBMIT` payloads), with bounded redaction. These assessments use
  `fleet-trace-assessments-v7`. It does not ask the judge to
  reconstruct evidence from a serialized full trace. Execution rationales cite
  real sandbox span IDs; malformed results, placeholder rationales, or unknown
  citations are scorer errors. The evidence summary includes at most eight
  execution spans; a trace with more spans fails scoring rather than judging an
  incomplete subset.
  Use this validated suite with `mlflow.genai.evaluate` for these two assessment names. A
  server-registered `{{ trace }}` judge with the same names has none of these
  checks and can record a pass for a rationale that cites nothing.
- `stopping_discipline`: judges actual computation code and execution output,
  including redundant methods within one code block. One target computation
  plus a supporting check is sufficient; failed checks, disagreement, or an
  explicit request for verification justify more work.
  Bootstrap is excluded. Up to eight task execution spans and 12,000 characters
  of code per span are supported; truncated evidence fails scoring. The judge
  must cite a supplied execution span when one exists. This is a diagnostic,
  not a GEPA objective or a host-enforced stopping guarantee. It adds one judge
  call per scored trace; no scoring call runs during an ordinary serving Turn.
- `output_instruction_compliance`: evaluates the complete redacted user request
  and final answer, up to 20,000 characters each. It does not replace the middle
  of either field with a preview. Missing, empty, oversized, or already truncated
  evidence produces a scorer error before any judge call. Diagnostic version
  `fleet-trace-assessments-v7` invalidates comparisons with earlier partial-text
  judgments; the shared `fleet-metric-v9` and GEPA objectives are unchanged.
- `agent_skills_progressive_disclosure`: evaluates selected Skill evidence,
  actual sandbox helper-execution receipts, or explicit Skill claims. Receipts
  cover `use_skill`, `read_skill_reference`, `run_skill_script`, and `save_skill`,
  even without TypeSafe selection. Each `sandbox.exec` holds at most 20 receipts
  with helper name, Skill name, and whether the helper returned, plus an explicit
  truncation flag. Bodies, script arguments, and results are excluded. A returned
  helper does not prove script exit status or compliance with its instructions.
  Catalog listing and code that merely mentions a helper do not activate this
  scorer; it is skipped when no applicable activity or claim is present.
- `rlm_trajectory_efficiency`: applies the shared efficiency formula to the
  host-recorded `fleet_turn` output. Nested predictor/model spans and tracebacks
  are not counted as additional calls or failures.
- `jev_routing_efficiency`: rewards fast paths without RLM/Flex and bounded
  RLM/Flex runs, using host-recorded budget units rather than wrapper spans. Both
  route classes are monotonic in budget units: a direct Turn keeps `1.0` up to two
  units and loses 0.02 per extra unit, and a Flex/RLM Turn keeps `0.95` up to fifteen
  and loses the same 0.02 per extra unit, floored at 0.2. This is a routing gauge,
  distinct from the GEPA efficiency term in `fleet_metric`.
- `route_correctness`: compares the turn's reported `route` (`direct`, `flex`,
  `rlm`) with the `route` expectation on a labeled case. It returns no value
  when a case has no gold route.
- `fleet_metric` (only when `judge_lm` is given): the same metric GEPA optimizes,
  logged as `fleet_metric` plus one `fleet_metric.<objective>` entry per objective.

Retrieval groundedness is applied only to traces containing a `RETRIEVER`
span. Inapplicable custom scorers return `None`, which MLflow skips, instead of
logging null feedback without an error. Unity Catalog's JSON-wrapped inputs
and outputs are decoded before deterministic, route, and Fleet metric scoring.
The GEPA objective uses `fleet-metric-v9`; changes to that metric invalidate
earlier metric feedback and candidate approval evidence. Candidate approval uses
policy version 6; older evidence requires reevaluation.

The v7 trace-assessment identity also invalidates comparisons with the earlier
routing rubric; Jev route probabilities retain their full precision in assessment metadata.
Trace diagnostic rubric changes use the independent
`fleet.trace_scorer_version` plus MLflow scorer name/version metadata. They do not change
`fleet-metric-v9` or GEPA candidate identity. The code-based Fleet suite is the
source for Fleet-managed evaluations; historical and manually created MLflow
assessments remain in the trace as audit history. Re-score a saved trace with
`score_evaluation_traces` to add new assessments without executing its Turn,
then filter feedback by `mlflow.assessment.sourceRunId` to inspect that scoring
run alone.
These custom code-based scorers run in offline evaluation; they are not
automatically registered for production monitoring.

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
`sandbox_errors`, `submit_rejections`) against the example. Each one applies only when its input exists:

| Objective | Applies when | Score |
|---|---|---|
| `answer_correctness` | always | `FleetJudge` verdict: `AnswerJudge` against the gold answer, or `TurnJudge` reference-free when there is none |
| `expected_values` | `expected_values` labelled | 1 if every value appears in the answer |
| `route` | gold `route` labelled | 1 if the Turn took that route |
| `artifact_correctness` | structured artifact gold | 1 if content and required Daytona verification match |
| `delegation_correctness` | structured child gold | 1 if child count, inputs, success and promoted hashes match |
| `efficiency` | explicit host usage counters | Measured execution efficiency; it contributes only after all applicable correctness checks pass |

`fleet_metric(gold, pred, trace=None, pred_name=None, pred_trace=None, *, judge_lm)`
returns `dspy.Prediction(score, feedback, objective_scores)`. `quality` is the
minimum of judge correctness/safety, labeled values and route, and applicable
artifact/delegation checks. `score` is zero if quality is below 1; otherwise it is
`0.9 + 0.1 * efficiency`. The judge must report both correctness and safety.
An empty answer scores zero without calling the judge. The feedback lists each
check and its reason, adds the expected answer, and names the component GEPA is
tuning during reflection.

`lm_calls` retains its public name and counts shared budget units: requested
generation/query completions, routing decisions, Skill decisions, and other
metered decisions. It is not a count of visible provider requests. The diagnostic
`budget_breakdown` categories sum to this counter. `sandbox_errors` counts remote
Python failures; `submit_rejections` counts blocking host submission rejections.
Learned-note warnings carry no penalty. Efficiency is:

```text
max(0, 1 - 0.015 * max(0, lm_calls - 3)
         - 0.15 * (sandbox_errors + submit_rejections))
```

All three counters must be explicit nonnegative integers. Missing legacy rejection
counts are unknown, not zero: efficiency is omitted, `usage_available` is zero,
and no efficiency credit or promotion evidence is granted. Existing feedback and
candidate evidence must be reevaluated under `fleet-metric-v9`; old traces remain
inspectable.
Latency and token usage remain diagnostic rather than weighted objectives.

### Metric feedback and JeV decisions

`fleet_metric` consumes the documented GEPA metric arguments: `gold`, `pred`, `trace`,
`pred_name`, `pred_trace`, and `program_trace` are positional, and the judge LM is
keyword-only. When GEPA supplies `program_trace` — only while optimizing a `dspy.Flex`
submodule — the feedback names the program's execution steps as bounded evidence. That
evidence does not move the score: identical predictions score identically with or without it.

TypeSafe/Jev serves decisions with a finite answer space that repeat, whose correctness can
be labelled, and where probability evidence is useful: route selection, Skill relevance, and
route suitability. Plans, generated code, summaries, Skill bodies, and explanations stay on
generative predictors, and no `RLM` or `Flex` output carries a decision type. The route
suitability judge keeps `Noul` at `JEV_ROUTE_THRESHOLD`. Calibrating the router's `Choice`
weights with DSPy's `ReAnchor` is deferred until a candidate artifact can carry fitted fields
into paired validation; installing calibrated fields after the gate would ship behavior the
gate never measured.

### Trace regression boundary

Trace `tr-e60ebd3bf370e42a8d5e724925de964b` has a sanitized diagnostic fixture
in `tests/fixtures/pi_digit_trace.json`. It returned `5`, with first supporting
checks complete at 106.999 seconds and Turn completion at 215.546 seconds.
Six generations plus one route and three Skill decisions explain ten budget
units. It recovered from one Python error and one submission rejection.
The accepted learned note substituted Python XOR (`^`) for executed exponentiation
(`**`). Historical stored notes are not rewritten by this remediation.

The fixture is captured execution evidence, not independently assessed gold or
an approved replay: this Turn did not select immutable replay capture. Scripted
RLM/Evaluate tests verify the submission contract without recomputing pi or
demonstrating improved live stopping. Claims about latency, stopping compliance,
or note fidelity require a separately authorized paired experiment with saved
trace IDs, correctness checks, usage, lifecycle timings, and a spend cap.

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

Compiling or optimizing does not score historical traces. `fleet eval run`
executes cases and scores only the traces it just produced.

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
memory (including the captured sandbox environment hint), selected dataset handle,
Skills catalog and revision, execution mode,
non-secret configuration, timeout, and Fleet version. Total workspace input is
limited to 8 MB; oversized
selected Turns fail before model execution. Snapshot bytes remain in Lakebase;
the GEPA dataset carries only the Session/Turn reference and digest.

GEPA accepts only approved gold-labeled traces whose selected snapshot exists,
passes its integrity checks, and matches the trace task, manifest, and file
digests. Unselected, missing, changed, or malformed snapshots are excluded. The
isolated rollout restores the captured file bytes and Skills revision rather
than reading the mutable current workspace or local Skills. The `compiled_program_digest`
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

Root outputs also carry host-derived `artifact_changes`: `created`, `modified`,
`retained`, `deleted`, or `unknown` when evidence is incomplete, with before/after
SHA-256 values. The existing `artifacts` list is the Session inventory, not a list
of files produced by that Turn. This diagnostic does not change scoring or gold
approval semantics. Prior answers in history and unreviewed learned notes remain
program inputs; a continuation with those inputs does not measure unseen-task
performance.
Fixed-size host usage counters precede artifact metadata in root outputs so
artifact-heavy Turns retain scoring evidence under trace sanitization limits.

Manual TypeSafe routing, Skill-selection, and batched-decision spans record
reported token counts. A total is derived only when both input and output are
known, and native DSPy tracking includes that usage once alongside generation.

## RLM-scale cases

- **Synthetic long context** (`--long-context`): whole-file counting, top-k, list, and needle tasks over
  generated logs of about 20K, 50K, 100K, and 200K characters. Gold values are computed from the log.
  `context_chars` and `answer_type` (`scalar` or `list`) are recorded so scores can be sliced by size and shape.
External task datasets are loaded by the [benchmark notebooks](../runbooks/benchmark-tasks.md).
Their official verifiers run outside the Fleet package. Fleet retains its DSPy metric,
judge suite, MLflow evaluation functions, and native GEPA/Flex optimization.
Benchmark traces carry `review_status=benchmark` and stay outside curated optimization datasets.
`expected_values_present` remains a generic containment check, not an official benchmark score.

Critical deterministic cases are explicitly labeled by a reviewer with `fleet eval label ... --critical` and one or
more `--value` expectations. Their complete source groups are pinned to holdout, and promotion requires every such
case to have quality 1.0 on every pass. Candidate approval fails closed when the holdout has no critical case.

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

Stopping instructions apply within generated code blocks as well as between
REPL actions. When multiple computation methods are used, the agent is
instructed to print each method's elapsed time and precision parameters.
Parameters retained in learned notes should be printed with the result;
omitting them from the note is also valid. These are model instructions;
offline contract tests do not establish live compliance or latency gains.
