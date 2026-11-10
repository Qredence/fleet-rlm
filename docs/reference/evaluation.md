# Evaluation

Evaluation scorers live in `src/fleet_rlm/optimize/scorers.py`. This page describes
the explicit evaluation workflows. `fleet eval` exposes dataset/run/label commands;
GEPA remains a deliberate isolated optimization workflow.

The default installation includes `databricks-agents`, which MLflow needs to
open and create Databricks Unity Catalog evaluation datasets. Dataset creation
falls back to a new dataset only when lookup reports that the dataset does not
exist; authentication, permission, and other lookup failures are surfaced.

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

For automatic assessments, inspect the registered scorer's schedule and sampling
configuration separately from this suite. Only interpret a result after the root
Turn has reached its terminal outcome and its required inputs, outputs, and spans
are present; an earlier assessment is not a verdict on the completed Turn. Check
the outcome as well as MLflow's status: historical traces can be marked `OK` even
when the Turn failed. Preserve old assessments as audit history and use a new
scoring run for a completed trace. Configuration changes and live scoring require
explicit authorization.

Serving traces now associate explicitly with a native MLflow LoggedModel using a
startup source/resource snapshot, installed dependencies, the effective configuration
digest, and the actually loaded DSPy state digest. Git commit/dirty status is metadata;
raw diffs are never uploaded. Concurrent Sessions use per-trace association, without
switching the process's active model. Source changes require a process restart.
Identity lookup waits at most 0.2 seconds; a slower lookup leaves that trace unversioned
and can populate the cache for later Turns. Identity failures are advisory and leave the
Turn outcome intact. Historical traces
remain unversioned unless their original model identity was recorded; evaluating an
old trace never assigns the current serving version to it.

Execution diagnostic rubric `execution-evidence-v2` uses MLflow's native boolean-map
result and its separate rationale, avoiding nested JSON encoded inside a string.
The rationale covers both assessments; strict boolean, receipt and citation validation
still applies. Recorded five-field JSON responses retain their original strict parser.

The portable `execution_evidence_diagnostics` registration imports the same validator
as the suite and reads the worker's `FLEET_JUDGE_MODEL` and provider environment.
Its two assessments retain the original target model ID and record executing code,
rubric, judge, evaluation run, and native registration identities separately.
See [assessment and latency rollout](../runbooks/assessment-latency-rollout.md) for
the read-only reconciliation review, saved-evidence validation, rollback, and opt-in
39-Turn comparison. These tools do not activate schedules or change generation defaults
merely by being installed.

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
  `fleet-trace-assessments-v8`. It does not ask the judge to
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
  `fleet-trace-assessments-v8` retains v7's invalidation of comparisons with earlier partial-text
  judgments. This diagnostic does not change the shared metric or GEPA objectives.
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
logging null feedback without an error.

### Chat-backed routing judges and registered definitions

The local routing wrapper accepts a trace but explicitly extracts host-owned
root task/route/usage fields before calling its judge. Construct the suite with
`FleetSettings.fleet_judge_model` and `jev_lm=None` for chat-backed routing
judgment; use the existing judge environment/model-format helpers for credentials
and provider routing. Missing host evidence raises a scorer error before the
chat call. Serving Jev and its typed rubric/threshold are unaffected.

A registered `InstructionsJudge` is separate from this wrapper. Even instructions
that reference only `{{ inputs }}` and `{{ outputs }}` can trigger MLflow's
agentic trace fallback if a failed trace has no root outputs. A decision-only
`gateway:/typesafe` model cannot perform that trace-based tool calling. Use a
chat-capable judge for trace evaluation, or the local wrapper's explicit
structured state; never manufacture success outputs to make a failed trace
judgeable. A route judgment is not evidence that the Turn committed.

Changing repository code does not update registered scorers. Before an explicitly
authorized registry replacement, retain the current version, complete definition,
and scheduling/sampling configuration; construct a chat-backed replacement with
the same rubric and validate it directly against representative saved traces.
Register only the intended scorer, read back its new identity/configuration, and
verify subsequent assessment metadata. Do not restore an old definition blindly
or change the eight-execution diagnostic limit to accommodate a historical trace.
Live evaluation and shared registry changes require explicit authorization.

In particular, `stopping_discipline` is included in the offline suite but may be
absent from a production trace's automatic assessments. Its absence is not a
passing verdict. Check the registered scorer's identity, schedule, sampling, and
assessment errors before attributing missing feedback to runtime wiring. Offline
regressions preserve complete chronological receipts for same-library precision
rechecks, immediate submission, failed checks, and requested verification. Their
scripted judge responses verify evidence transport and rubric contracts; they do
not demonstrate that a live judge classifies those cases correctly.

Unity Catalog's JSON-wrapped inputs
and outputs are decoded before deterministic, route, and Fleet metric scoring.
The GEPA objective uses `fleet-metric-v10`; changes to that metric invalidate
earlier metric feedback and candidate approval evidence. Candidate approval uses
policy version 6; older evidence requires reevaluation.

The v7 trace-assessment identity also invalidates comparisons with the earlier
routing rubric; Jev route probabilities retain their full precision in assessment metadata.
Trace diagnostic rubric changes use the independent
`fleet.trace_scorer_version` plus MLflow scorer name/version metadata. They do not change
`fleet-metric-v10` or GEPA candidate identity. The code-based Fleet suite is the
source for Fleet-managed evaluations; historical and manually created MLflow
assessments remain in the trace as audit history. Re-score a saved trace with
`score_evaluation_traces` to add new assessments without executing its Turn,
then filter feedback by `mlflow.assessment.sourceRunId` to inspect that scoring
run alone.
These custom code-based scorers run in offline evaluation; they are not
automatically registered for production monitoring.

## Databricks experiment layout and saved-trace scoring

Fleet uses the Databricks `/Shared/fleet-rlm-runtime` experiment for runtime
traces and trace-level scoring. Keep the curated evaluation dataset and repeatable
offline evaluation runs in `/Shared/fleet-rlm-eval`; keep GEPA runs in
`/Shared/fleet-rlm-gepa`. The selected workspace profile and all three absolute
experiment paths live in `config/fleet.toml`. Backend selection remains explicit,
so local tracking stays the default unless a command selects Databricks.

To validate Fleet scorers on a small historical sample without executing new
Turns, read completed, nonduplicate traces from the runtime experiment and pass
only eligible scorers to `score_evaluation_traces`. Set the named Databricks
profile and warehouse before trace reads; pass the runtime experiment ID so the
scoring run is recorded alongside the traces. Do not include retrieval scorers
when the sample has no retriever spans, or expectation-based scorers when the
sample lacks matching labels. Preserve prior assessments and inspect only new
feedback with `mlflow.assessment.sourceRunId` equal to the returned evaluation
run ID.

```python
import os
import mlflow
from mlflow import MlflowClient
from databricks.sdk import WorkspaceClient

from fleet_rlm.config import FleetSettings
from fleet_rlm.optimize.evaluation import score_evaluation_traces
from fleet_rlm.optimize.scorers import build_mlflow_scorer_suite

settings = FleetSettings(
    mlflow_mode="databricks",
    mlflow_databricks_experiment_name="/Shared/fleet-rlm-runtime",
)
settings.prepare_mlflow_auth()
target = settings.mlflow_target()
mlflow.set_tracking_uri(target.tracking_uri)
os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] = settings.mlflow_tracing_sql_warehouse_id
workspace = WorkspaceClient()
if workspace.config.auth_type == "pat":
    os.environ["OPENAI_API_KEY"] = workspace.config.token
elif workspace.config.auth_type == "databricks-cli" or workspace.config.auth_type.startswith("oauth"):
    os.environ["OPENAI_API_KEY"] = workspace.config.oauth_token().access_token
else:
    raise ValueError(f"Unsupported Databricks auth type: {workspace.config.auth_type}")
os.environ["OPENAI_API_BASE"] = workspace.config.host.rstrip("/") + "/ai-gateway/mlflow/v1"

experiment = MlflowClient().get_experiment_by_name(target.experiment_name)
if experiment is None:
    raise RuntimeError("Runtime experiment was not found")
trace_ids = ["trace:/example_catalog.fleet_observability.fleet_rlm_runtime/TRACE_ID"]
traces = [mlflow.get_trace(trace_id, silent=True, flush=True) for trace_id in trace_ids]
suite = build_mlflow_scorer_suite(settings.fleet_judge_model)
eligible_names = {"output_instruction_compliance", "rlm_trajectory_efficiency", "jev_routing_efficiency"}
selected = [scorer for scorer in suite if scorer.name in eligible_names]
result = score_evaluation_traces(
    traces,
    judge_model=settings.fleet_judge_model,
    experiment_id=experiment.experiment_id,
    scorers=selected,
)
print(result.run_id, result.metrics)
```

Check task and answer evidence locally before choosing trace IDs; do not print
payloads into logs. Add `route_correctness` only for traces carrying reviewed route
expectations. This explicit scoring workflow does not register a production scorer
or activate sampling. Databricks production scorers are experiment-scoped, and
custom remote scorer serialization has separate runtime constraints; validate
those constraints before registering any scorer for ongoing monitoring. The
Databricks registry also requires a `databricks:/` judge model for production
scorers; Fleet's `openai:/` Gateway model can be used for an explicit offline
scoring run but cannot be registered for remote monitoring as-is.
Production monitoring is Beta. Start registered scorers stopped, inspect the
definition and sample rate, then use an explicitly authorized canary before
enabling low-rate sampling.


## Metrics

Expected numeric values match complete signed decimal or exponent tokens, with
optional correctly grouped thousands commas. Numeric equality is exact (Decimal,
no tolerance): `12` rejects `-12` and `12.5`, and `1` rejects `1.25`; `+12`,
`12.0`, and `1.2e1` are equal. Text expectations retain case-insensitive word
matching and comma removal. Presence is not proof of answer correctness; semantic
judging remains separate. This correction advances the shared scorer to v10.

Native DSPy GEPA tunes the Turn program (`FleetProgram`) end to end,
including predictor instructions and generated Flex code through DSPy's built-in
code proposer. It uses the current deployment settings, including persisted
Session overrides. The manifest records effective model configuration for
provenance; replay uses the current deployment configuration and credentials.

Its components are:

- `routing.predict` (`TurnTriage`): automatic Direct/RLM route choice; explicit Flex bypasses it
- `skill_selection.predict` (`SkillRelevance`): optional Skill relevance
- `reasoning.direct.predict` (`DirectAnswer`): the no-code answer
- `reasoning.rlm.generate_action` and `reasoning.rlm.extract` (`FleetTask`): the native RLM loop
- `reasoning.flex`: native Flex code parameter (not an ordinary persistent predictor)
- `child_policies.leaf_action` and `child_policies.delegating_action`: native child action policies.

The ordinary `FleetRuntime` constructs and binds the learned graph. Serving and
fresh isolated rollout runtimes use the same host invocation for dataset integrity,
Turn scope, callbacks and usage/artifact receipts. Learned output cannot certify its
own execution evidence. Outer owners still persist/finalize serving Turns or confirm
disposable rollout cleanup; neither lifecycle moves into the learned root.

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

`fleet_metric(gold, pred, trace=None, pred_name=None, pred_trace=None,
program_trace=None, *, judge_lm)` returns
`dspy.Prediction(score, feedback, objective_scores)`. The first five arguments
remain the metric's existing positional contract; `program_trace` is the optional
sixth GEPA argument, declared so GEPA can pass bounded Flex execution evidence.
`quality` is the minimum of judge correctness/safety, labeled values and route,
and applicable artifact/delegation checks. `score` is zero if quality is below 1;
otherwise it is `0.9 + 0.1 * efficiency`. The judge must report both correctness
and safety. An empty answer scores zero without calling the judge. The feedback
lists each check and its reason, adds the expected answer, and names the component
GEPA is tuning during reflection.

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
candidate evidence must be reevaluated under `fleet-metric-v10`; old traces remain
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

### Experiments in the Fleet TUI

Use `/optimize` to open the Experiments screen and start or focus a GEPA job. Jobs,
labels, and the installed program are global: every Session contributes examples and
every Session serves the approved program.
The API owns the job and runs the existing CLI optimizer in a subprocess. Its
lifecycle is recorded in PostgreSQL; candidate files remain in the configured
artifact directory and MLflow stores the optimization run, traces, and evidence.
The API admits one job or review and does not queue requests.
The screen polls snapshots once per second and lists the history of every Session for
completed, failed, cancelled, rejected, and approved runs. Select a run to inspect
its saved comparison, exact before/after component text, bounded diffs, objective
and quality changes, gate reasons, and sanitized validation failures. Approve
rechecks the candidate's current evidence; Reject records an explicit decision
without deleting candidate artifacts.

The client owns neither the job nor its cancellation: disconnecting does not stop
work, while the explicit cancel action requests cooperative stopping. A normal
launcher shutdown drains its API-owned job before stopping the API and MLflow.
An API restart fences abandoned work as interrupted without automatically rerunning
it; interrupted approvals are reconciled against active artifact digests before
they can be reviewed again. Standalone `fleet optimize` remains a separate CLI
workflow and can also list/approve its stored run records.

Runs recorded before the program became global are shown with their former Session
and cannot be approved. `fleet optimize --check` and `GET /improve/readiness` report
whether the labelled examples satisfy a run's requirements before any live work.
A job's token cap (`--max-total-tokens`, `max_total_tokens`, `FLEET_JOB_MAX_TOKENS`)
ends it as `budget_exhausted`; during search `progress` also reports `best_score`
and `score_trajectory` from GEPA's validation-set callback.

Progress shows named lifecycle stages when no counter is available. During search,
the counter is GEPA metric calls used/total as reported by its budget callback; it
is not a provider-request or LM-call count. Cancellation is cooperative: the TUI
requests a stop, remains responsive while in-flight work finishes, and reports the
terminal outcome only after cleanup.
An accepted stop during finalization still cancels the run. Files already written
remain as diagnostic evidence and are unavailable for approval. A stop received
after the completed outcome is saved does not change that outcome. Cleanup
failure is reported as failure, and the TUI waits for the subprocess to exit and
its output to drain before displaying its terminal event.

Measured LM-call totals, median per-example runtime, and artifact-pass counts are
diagnostics with sample counts and applicability. They do not independently pass
or fail the promotion gate. Promotion still requires paired dev/holdout quality,
critical-case, execution-error, and measured-usage checks described above. A
`--max-metric-calls 10` configures the search metric-call budget; GEPA reflection,
judging, and final paired validation can make additional calls and incur additional
cost. Native GEPA checks this budget between batches and can overshoot it.
For a bounded canary with three dev examples, add
`--reflection-minibatch-size 1 --max-candidate-proposals 1`: seed scoring,
one parent/child minibatch, and an optional full candidate evaluation use at most
eight search metric calls. The proposal limit uses GEPA's native stopping rule;
ordinary runs retain their existing defaults. Optimization does not promote a
candidate; approval is a separate action. Search rollout traces are disabled by
default; `--trace-search` opts into them. Final paired validation emits per-case
traces tagged with stage, split, pass, and case identity in the same MLflow run.
Async trace writes are flushed before a run can reach its terminal state.

## Explicit historical trace evaluation

Compiling or optimizing does not score historical traces. `fleet eval run`
executes cases and scores only the traces it just produced.

## DSPy integration rationale and validation

The integration follows the locked DSPy 3.4.1 and GEPA 0.1.4 contracts. These
were the latest published stable releases checked on 2026-10-25; `uv.lock` is
authoritative for installed packages. DSPy 3.4.1 treats each
Flex as a whole source-code component; predictors inside that Flex are owned by
the source and are not separately tuned. Ordinary predictors remain instruction
components. DSPy supplies native instruction reflection using its JSON adapter,
Flex code proposal, component selection, and candidate selection. Fleet sets
bounded instruction length and task guidance, supplies the metric and isolated
runtime, and uses DSPy's `SandboxLM` to account host-mediated calls against the
shared Turn budget. GEPA's file stop and candidate-proposal
callbacks are isolated version-specific integration points from GEPA 0.1.4;
search metric call limits remain advisory between native batches. See the [versioned Flex
contract](https://dspy.ai/3.4.1/api/modules/Flex/) and [GEPA
API](https://dspy.ai/3.4.1/api/optimizers/GEPA/overview/).

Explicit Fleet child calls expose `child_policies.leaf_action` and
`child_policies.delegating_action` as ordinary native instruction components outside
Flex source ownership (`program.`-prefixed in the isolated wrapper). Fresh native
child RLMs receive independent copies of the selected policy's instructions and
demos, not its LM or live runtime. DSPy 3.4.1's GEPA adapter attributes predictor traces by
signature equality; immutable role-specific `code` field descriptions distinguish
the policies even when their instruction strings match. Child extraction and
arbitrary RLMs authored inside Flex remain outside this separate policy inventory.
The offline integration test executes both RLM and Flex through the emulated
Daytona transport and native GEPA trace/reflection APIs, including simultaneous
children and a leaf-only candidate mutation. This proves attribution and state
transfer, not improved model quality or live-provider behavior.

Ordinary child adapter parse failures still return as tool errors without ending
the Turn. Native DSPy does not record those swallowed exceptions as failed child
predictor steps, so native GEPA cannot produce child-specific format-failure
reflection for them. The offline test checks this limitation explicitly; no
synthetic trace injection or change in child error severity is introduced.

LM and serving-adapter overrides use scoped `dspy.context` calls. DSPy's evaluator
owns worker context propagation. Fleet uses native predictor/parameter traversal
and native state loading. Missing RLM limits are supplied only by the outer Flex
interpreter's constructor adapter, not by source rewriting or a second state load.
DSPy support is pinned to the tested 3.4.1 release because this facade protocol is internal. JSON state preserves optimized instructions
and Flex source; the host reconstructs the Daytona interpreter and current
credentials. Installation now requires a complete host envelope with
`format="fleet-program"`, `format_version=1`, `program_schema="fleet-program-b1"`,
`child_policy_contract=1`, canonical unwrapped native `state`, and string-valued
`metadata`. The exporter deliberately reads the isolated wrapper's `.program`;
native `.save()` output is not an installation artifact. Missing/extra parameters,
incompatible field contracts, malformed Flex source and any non-null learned LM
binding are rejected before any state changes. Native field criteria/configuration
are preserved and validated by DSPy, not translated by Fleet.

Gate A `fleet-program-a1` envelopes are incompatible with the two-way automatic
route contract. They remain inspectable, not installable. Explicit baseline
selection or a newly reviewed/revalidated B1 artifact is required; there is no
automatic transplant of old instructions, demos or criteria. Native parameter
paths and `child_policy_contract=1` are unchanged. Route assessments use
`jev-route-v2` / `fleet-trace-assessments-v8`: bounded code, file and Skill work
can reasonably use RLM; explicit Flex remains valid. The independent route judge
still has three choices and the same Noul threshold and evidence requirements.

Old bare/wrapped JSON and older child-policy markers are readable as historical
artifacts only. They cannot be approved or installed into this schema. Do not rename
keys to simulate migration: reconstruct/re-evaluate a new candidate under the running
schema, or explicitly select its baseline. For an operator-approved baseline reset,
retire the active `fleet.json` while the runtime is stopped; do not delete an
artifact alone. No new selection endpoint or automatic rollback is added by Gate A.
An unconfigured library runtime (`compiled_path=None`) explicitly uses baseline.
An absent `fleet.json` means baseline; a present one is the program every Session
serves, picked up at the start of its next Turn, and its digest is recorded as
`compiled_program_digest` and in the `program_selection` event. There is no per-Session
selection setting. A present but invalid file is not replaced by baseline: invalid
replacement bytes retain the valid in-memory program/digest but refuse the Turn. No live interpreter state is restored.

Runtime and configuration digests, a digest of optimization source,
and a versioned fingerprint of installed distribution names and versions are
captured before search and rechecked before the candidate manifest is written and
again at approval. Evidence format 2 does not depend on finding a source-tree
`uv.lock` at runtime.
Source identity includes engine/optimizer/sandbox Python (including additions and
removals), snapshot requirements, bundled Skill resources, and the shared
configuration, contracts, instrumentation, evidence, replay, workspace, Skills,
session, and Store owners. Presentation-only API/TUI code is excluded. Historical
evidence remains readable, but changed code, dependencies, scorer, configuration,
incumbent, or candidate bytes require reevaluation before installation. Static
recipe identity does not prove every remote environment's identity.

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

### Offline native boundary checks

The locked source paths are `dspy.primitives.facade.FacadeInvocation.install`,
`construct`, `_build_predictor`, and `call`; Flex's `BridgeRuntime` installs the
shim and registers `__dspy_construct__` with nested `kwargs`. Native signature
resolution, tool decoding, handles, LM scoping, and factory propagation remain
native. Fleet copies only omitted `max_iters` / `max_llm_calls`, leaving explicit
values (including `None`) and malformed payloads to native behavior. Propagated
factories do not pass these defaults into deeper interpreters.

`tests/sandbox/test_native_defaults.py` executes real native Flex source through
an emulated Daytona transport, including aliases, expanded kwargs, multiple
constructors, source save/load, factory isolation, and host-budget exhaustion.
`tests/lite/test_dspy_contracts.py` covers native query metering, child lifecycle,
and success-only promotion. Semantic `llm_query` calls are not code-executing
children. Native nested RLMs retain the Daytona backend and host cleanup;
Fleet-managed `spawn_agent` / `spawn_agents` retain input selection, allowlisted
tools, slices, concurrent contexts, findings/artifact contracts, and failure
classification. Primary RLM actions use root LM and queries use sub LM; Flex's
baseline RLM inherits root LM, while explicit children use sub LM. No child
scheduler or adapter equivalence is claimed by this consolidation.

Validation still runs three paired passes before component comparison, even for
unchanged candidates. No early no-candidate outcome, unattended recovery, or
measured optimization gain is introduced.

## Judges

MLflow scorers use `FLEET_JUDGE_MODEL`. `fleet_metric` uses `FleetJudge`, a
`dspy.Module`, on its DSPy twin: `FleetSettings.build_judge_lm`, configured through
the gateway. Judge nondeterminism is possible and must be measured. GEPA reflection
uses `FLEET_REFLECTION_MODEL`; the checked-in configuration currently uses Claude
Haiku 5.5 at temperature 1.0 with a 32,000-token output budget.

## Trace contents and selected replay evidence

The `fleet_turn` root span records `session_id`, `turn_id`, task, workspace
manifest, file digests, whether replay capture was selected, and the snapshot
digest. Capture is on by default (`record_replay: false` opts out). A selected Turn stores an immutable snapshot
in `fleet_lite_replay_snapshots` in the same transaction as successful Turn
completion. It contains bounded exact workspace file bytes, conversation history,
memory (including the captured sandbox environment hint), selected dataset handle,
Skills catalog and revision, execution mode,
non-secret configuration, timeout, and Fleet version. Total workspace input is
limited to 8 MB; an oversized
workspace skips capture (a `replay_skipped` event, `replay_snapshot_selected` false) and
the Turn proceeds. Snapshot bytes remain in Lakebase;
the GEPA dataset carries only the Session/Turn reference and digest.

The validation ratio is inclusive `0.1–0.5`, default `0.35`, across CLI, HTTP,
slash commands, and direct curation. After grouped curation, nonempty train/dev/
holdout splits and at least one critical holdout case are required before native
search. Failure diagnostics show example/group counts and retain the existing
failed-preparation lifecycle record.

GEPA accepts only approved gold-labeled traces whose selected snapshot exists,
passes its integrity checks, and matches the trace task, manifest, and file
digests. Unselected, missing, changed, or malformed snapshots are excluded. The
isolated rollout restores the captured file bytes and Skills revision rather
than reading the mutable current workspace or local Skills. Each rollout constructs
a fresh manager and engine, then loads a deep copy of the bare candidate engine's
native DSPy state. Candidate state is transferred in memory, with no temporary
candidate JSON file. Setup failures shut down the manager in the factory; after
setup, the isolated program owns shutdown on success or execution failure. Cleanup
errors remain visible to the caller.

Root, sub and Jev model roles, execution mode and Turn budgets come from the
current deployment constructors. All non-null learned predictor/Flex LM bindings
are rejected. Published candidates use the complete `fleet-program-b1` envelope,
manifest/digest approval and the bounded serving loader described above. Historical
bare state, `program.` wrapper keys and `fleet_child_policy_version` markers are
not accepted for installation. This code/schema change invalidates prior approval
evidence; collect fresh evidence before approving a candidate.

The envelope's `child_policy_contract: 1` identifies the two required native action
policies; it is not a tunable parameter. Incomplete state, modified role field
contracts or saved LM overrides are rejected before changing the incumbent.
Isolated reconstruction uses the same complete native-state validation in memory.
Runtime limits and tool allowlists remain authoritative, including nondefault
slices announced in child inputs; no saved policy restores a live interpreter.

The `compiled_program_digest`
attribute names the approved program that served the Turn (empty when none is
installed). Selected replay snapshots expire after 30 days and are pruned when
the Store initializes; their Lakebase curation rows are deleted with them. Do
not leave capture on for data that is not approved for this use.
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
SHA-256 values. Host evidence collects up to 100 `artifacts/` files totalling at
most 25 MB. The persistent Session inventory can grow beyond these byte-evidence
bounds: exceeding either bound marks the evidence incomplete, with change states
`unknown`, while the Turn retains the full path inventory and can complete.
Malformed byte evidence and incomplete child lifecycle receipts still fail the Turn.
Reviewed gold keeps a separate, much smaller limit because it is review metadata,
not output. Structured scoring continues to require complete evidence.
The existing `artifacts` list is the Session inventory, not a list
of files produced by that Turn. This diagnostic does not change scoring or gold
approval semantics. A root rewrite or removal of promoted child output records
`superseded_child_receipts` in the final capture; rewritten files use workspace
provenance, while the original promotion receipt remains in execution evidence.
Prior answers in history and unreviewed learned notes remain
program inputs; a continuation with those inputs does not measure unseen-task
performance.
Fixed-size host usage counters precede artifact metadata in root outputs so
artifact-heavy Turns retain scoring evidence under trace sanitization limits. The
exported trace preview clips `execution_evidence.artifacts.files` to 100 entries
and records `previewed` and `total`; the stored Turn capture is never clipped.

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

Configuration comparison uses three repetitions each of direct answer, Flex arithmetic,
file analysis and explicit children, plus one large-Pi canary per arm (39 Turns maximum).
Compare root/sub caps 16384/provider-default, 8192/8192 and 4096/8192 with other generation
settings unchanged. Keep shared learned notes disabled and gold outside agent inputs.
Promotion requires at least 10% lower median short-task latency, no correctness or
truncation regression, no aggregate observable generation-call increase and a successful
Pi canary. Missing version or finish-reason evidence blocks promotion. Otherwise retain
current defaults. ReAnchor remains deferred; native GEPA integration is covered by the offline and live canaries.

Stopping instructions apply within generated code blocks as well as between
REPL actions. When multiple computation methods are used, the agent is
instructed to print each method's elapsed time and precision parameters.
Parameters retained in learned notes should be printed with the result;
omitting them from the note is also valid. These are model instructions;
offline contract tests do not establish live compliance or latency gains.
Only exact observed representations belong in notes: scaled `1.49M` and unprinted
`guard=100` can produce advisory numeric-grounding warnings. A warning never blocks a
verified answer. Do not add an execution solely to manufacture an optional learned note.

## Optimization ledger ownership

The optimizer worker is the sole durable writer of running stages and search counters
for both standalone CLI and API-managed experiments. GEPA budget callbacks persist
one coherent snapshot per observation. Partial callbacks and stage changes retain
known counters; the final callback updates the cumulative metric-call total without
clearing iteration, delta, or remaining observations. Unknown remaining budget stays
unknown. Only the cumulative metric-call count is monotonic.

| Responsibility | Authority and failure policy |
| --- | --- |
| Admission, options, owner leases and process startup | API for managed runs; CLI for standalone runs. Required Store persistence. |
| Session settings, reviewed replay data, incumbent/evidence identity | Worker reads and records required data; errors block success. MLflow-backed replay input access remains required. |
| Running preparation, search, validation and finalization stages/counters | Worker only, with Store owner/status compare-and-swap fences. |
| Candidate paths, manifest digest, gate and dataset sizes | Required worker Store writes backed by local artifact/evidence writes. Failure prevents eligible completion. |
| Cancellation intent | API commits the database stop flag before signaling the stop file. Worker checks both at native safe points and stage boundaries. |
| Managed worker outcome | Worker records the required database handoff; stdout terminal events have no authority. |
| Managed terminal outcome | API confirms process exit and drains both pipes before owner/status-fenced terminal arbitration. Accepted cancellation overrides completion. |
| Standalone terminal outcome | Worker persists after required cleanup and cancellation/ownership checks, before releasing its lease. |
| MLflow setup, tracking logs and trace flush; CLI diagnostic events | Best effort, with sanitized diagnostics. These failures do not set the required-persistence stop latch. Approval evidence flush policy is unchanged. |

The API drains stdout and stderr in fixed-size chunks without parsing progress or
retaining a diagnostic tail. Delayed progress or stage events cannot replay older
observations into PostgreSQL. Worker callback/control events are omitted on pipes;
the supervisor uses the database and keeps draining native optimizer output even
when the client disconnects. Explicit cancellation remains cooperative, and does not
promise immediate interruption of an in-flight provider call.

The reporter retains the first required-write failure across callback threads and
adds its predicate to native GEPA stop callbacks alongside the file and database
stoppers. Explicit checks after compile and before validation, publication and
success finalization reject partial results even if native callback dispatch catches
an exception. Ownership loss is a persistence failure; accepted stop intent is
cancellation. Local candidate files alone never authorize approval of a failed run.

No SQL migration or new wire fields are required. Historical progress snapshots
remain readable. Deploy API and worker changes together after draining old pairs:
an older API can otherwise continue replaying stdout. Roll back the pair together;
do not re-enable concurrent stdout replay into worker-owned progress.
