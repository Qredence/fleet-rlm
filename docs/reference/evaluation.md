# Evaluation and optimization

Use `fleet eval` to collect, label and score cases. Use `fleet optimize` to
create a GEPA candidate, then approve it separately. Live execution and judging
require explicit authorization. For a first check, follow the
[evaluation canary](../runbooks/evaluation-canary.md).

## Collect and score

Run evaluation against a dedicated server configured for the evaluation MLflow
experiment and trace destination. The client verifies that destination before
execution. Each invocation creates fresh Sessions and prints Session/trace IDs.

```bash
uv run fleet eval run --url http://127.0.0.1:8001 --limit 1
uv run fleet eval label TRACE_ID --answer 'Reviewed answer' --value 22.2
uv run fleet eval score TRACE_ID
```

After execution, recover readback, labeling or scoring on the saved trace.
Running `eval run` again executes new Turns. A scoring error means incomplete
evaluation; a wrong answer is a result to retain. Inspect terminal Turn outcome
and complete spans before interpreting assessments.

Serving traces carry the original agent's source, configuration, dependency and
loaded-program identities when available. Scoring preserves that identity and
adds its own evaluation run. Filter assessments by `mlflow.assessment.sourceRunId`
to distinguish a scoring run from earlier feedback.

## Scorers and metric

[`optimize/scorers.py`](../../src/fleet_rlm/optimize/scorers.py) owns the suite:

| Scorer | Purpose |
| --- | --- |
| Relevance and retrieval groundedness | MLflow judges; retrieval applies only with a retriever span |
| Route suitability | Jev `Noul` at `JEV_ROUTE_THRESHOLD` plus route `Choice`, or the configured generative judge; records backend and evidence |
| Execution diagnostics | One judgment emits verification discipline and tool integrity from host execution receipts |
| Route correctness | Compares the selected route with an explicit gold route |
| `fleet_metric` | Shared DSPy/GEPA correctness and efficiency objective |

Execution rubric `execution-evidence-v3` requests a JSON-object string containing
three booleans (`verification_applicable`, `verification_pass`, `integrity_pass`)
and two rationale strings (`verification_rationale`, `integrity_rationale`).
Python validates types and actual `span_id=` citations. More than eight execution
spans, malformed results or fabricated citations fail scoring. Sandbox success
alone does not establish answer correctness; evaluated content is data.

`fleet_metric(gold, pred, trace=None, pred_name=None, pred_trace=None,
program_trace=None, *, judge_lm)` returns
`dspy.Prediction(score, feedback, objective_scores)`. It preserves the first five
positional arguments; optional Flex execution evidence enriches feedback.

| Objective | Applies when |
| --- | --- |
| Answer correctness and safety | Always; an empty answer scores zero |
| Expected values | Values are labeled; numeric equality uses exact Decimal tokens |
| Route | A gold route is labeled |
| Artifact/delegation correctness | Structured gold and complete host receipts exist |
| Efficiency | Explicit nonnegative usage counters exist and every correctness check passes |

Quality is the minimum applicable correctness score. Below quality 1, score is
zero; otherwise score is `0.9 + 0.1 * efficiency`, where:

```text
efficiency = max(0, 1 - 0.015 * max(0, lm_calls - 3)
                       - 0.15 * (sandbox_errors + submit_rejections))
```

`lm_calls` counts shared budget units, not wire requests. Missing usage stays
unknown and grants no efficiency or promotion credit. Latency/tokens are diagnostics.
The metric is `fleet-metric-v10`; approval policy is version 6.

## Judge configuration

Serving, judging and reflection use separate roles. Configure `[llm.judge]` for
the DSPy metric, `[evaluation].judge_model` for MLflow, and `[llm.reflection]` for
GEPA. Use the existing model-format and judge-environment helpers. For an
OpenAI-compatible endpoint, DSPy uses `openai/<model>` and MLflow uses
`openai:/<model>`; the resolved judge connection supplies `OPENAI_API_BASE` and
`OPENAI_API_KEY`. Keep credentials on the host.

```bash
uv run fleet eval score --execution-diagnostics-only TRACE_ID
uv run fleet eval score --scorer-experiment-id ID \
  --registered-scorer execution_evidence_diagnostics TRACE_ID
```

The two selectors are exclusive. Registered scoring reads trusted definitions,
records/rechecks native versions and digests, and leaves schedules unchanged.
Repository changes do not update server registrations automatically.

## Reviewed replays and readiness

Replay capture defaults on; `record_replay: false` or `/replay off` disables it.
A successful Turn can commit up to 8 MB of exact workspace input with conversation,
memory, dataset handle, Skills revision and safe configuration. Oversized input
skips capture and emits `replay_skipped`. Credentials are excluded.

Only explicitly approved gold with an intact matching snapshot enters GEPA.
Benchmark traces (`review_status=benchmark` or `fleet.benchmark.*`) are excluded.
Gold stays outside predictor inputs. Group by source lineage to prevent leakage.
Labelled snapshots are retained; unlabelled snapshots expire after 30 days.

```bash
uv run fleet optimize --check
```

Readiness requires nonempty train/dev/holdout, at least three source groups,
minimum dev/holdout sizes (`FLEET_MIN_SPLIT_EXAMPLES`, default 5), and a critical
holdout. `validation_ratio` is 0.1–0.5, default 0.35. Critical cases require
expected values and pin their entire source group to holdout.

## Search, validate and approve

Native DSPy GEPA tunes predictor instructions and Flex source. ReAnchor fits
route/Skill decisions on train only; dev controls adoption. Holdout never enters
search. Each rollout reconstructs captured inputs in an isolated runtime with
current deployment roles and budgets; stored history cannot restore an interpreter.

The candidate and incumbent receive three paired passes on dev and holdout.
Promotion requires no aggregate quality regression, improved dev composite score,
no holdout composite regression, zero holdout execution errors, measured usage,
and quality 1 on every critical case in every pass. Unchanged candidates skip
validation and cannot pass as improvements.

`--max-metric-calls` bounds search calls between native batches; reflection and
paired validation are additional work. `--max-total-tokens` uses reported
usage and cooperative checks, so it can overshoot. Neither is a dollar ceiling.
`--trace-search` enables search traces; final validation records per-case traces.

Approval rechecks candidate bytes, incumbent, configuration, code and dependency
identities before installing the global `fleet.json`. The complete
`fleet-program-b3` envelope contains native unwrapped state, child policies and
string metadata. Saved LM bindings are rejected; the runtime supplies models,
credentials and adapters. Every Session picks up approval at its next Turn.
After approval, a missing or changed installed file refuses execution.

## Improve jobs

`/improve` provides Review, Readiness, Runs and Results tabs. Jobs and the
installed program are global. One job or review runs at a time, without a queue.

- **Optimize:** creates a candidate; explicit review installs it.
- **Rescore:** judges saved executions; it does not measure the current program.
- **Replay:** measures the installed program in isolated rollouts.
- **Synthetic:** currently disabled.

The worker owns stages/counters and required persistence. Required writes failing
stop search and block publication; optional telemetry does not. The API drains
bounded private logs and records the managed outcome after process exit.
Cancellation checks both database and stop file. Client disconnect does not cancel
jobs; API restart marks abandoned jobs interrupted without rerunning them.
Auto-run defaults off, requires a nonzero token cap, and creates candidates only.

## Native DSPy boundary

Runtime and snapshot pin DSPy 3.5.0. Fleet uses native LMs, adapters, typed
`Choice`/`Noul`, `SandboxSession`, GEPA, ReAnchor and state traversal. Scoped
`dspy.context` supplies role overrides. Rescore workers configure callbacks inside
their one-job process because MLflow scorer threads do not inherit that context.
Dataset serialization goes one way into Daytona; guest views return JSON metadata,
not host-executable codecs. Generated Python and nested code run in Daytona.

Offline coverage lives in `tests/engine/test_runtime.py`,
`tests/sandbox/test_sandbox.py` and `tests/optimize/test_optimization.py`.
Scripted models and emulated transport verify contracts, not live compatibility
or measured gains. See [external benchmarks](../runbooks/benchmark-tasks.md) for
notebook protocols and official scoring.
