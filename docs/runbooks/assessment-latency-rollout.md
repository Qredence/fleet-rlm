# Assessment and latency rollout

Source changes and offline tests do not authorize live activation. Restarting servers,
creating LoggedModels, registry writes and provider/judge calls require explicit approval.
Deploy trace association first, reconcile experiment 1 second, and compare generation
caps last. Preserve trace `tr-6935d04dc15442cfb7a7dd11597cecee` and its existing feedback;
the reconciliation uses captured receipts and never executes that Turn again.

## Agent identity

Tracing startup freezes serving Python and bundled resources plus installed distribution
names/versions and Python version. The serving snapshot excludes optimizer/TUI modules,
hidden files, working-directory data and request/Session/memory/dataset contents. It works
from an installed wheel without Git or a lockfile. The existing effective configuration
digest and native loaded DSPy state digest complete `fleet-agent-v1`; LM clients and their
credentials are excluded from the state digest. Only hashes and curated commit/dirty
metadata go to MLflow. Neither the dirty flag nor unrelated working-tree files determine
the fingerprint. No automatic Git versioning or raw diff upload is enabled.

Native LoggedModel search/create reuses `fleet.agent.fingerprint`, with a readable
`fleet-rlm-<prefix>` name. The process caches by destination, experiment and fingerprint.
Before each new Turn's root closes, including failures, `update_current_trace(model_id=...)`
associates that trace explicitly. The loaded state is read after execution/reload, rather
than hashing an artifact that might have changed on disk. No global active-model context
is changed. Identity observation errors report only exception types and cannot reverse
the Turn outcome. Check `fleet.agent.model_id`, fingerprint and source/configuration/program
digests before accepting a comparison trace. Source edits require a process restart.

## Prepare the owned scorer worker

The pinned MLflow 3.17.2 SQL store currently rejects automatic scheduling for decorator
scorers: it requires a serialized gateway model, while an import-only scorer exposes
function source rather than a model field. `MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS` enables
code reconstruction but does not remove that eligibility requirement. Live validation
can succeed while `start()` remains unsupported. In that case keep the combined scorer
inactive and preserve the old schedules. Do not bypass the store guard or replace the
strict routine with an unchecked native judge. An externally scheduled explicit evaluation
job is a separate rollout choice; this runbook does not install one automatically.

Install the reviewed package in the owned MLflow server/job-executor environment. The
owned launcher explicitly sets `MLFLOW_SERVER_ENABLE_CUSTOM_SCORERS=true` on the server it
starts. Reused servers are untouched: their operator must opt in explicitly. This enables
MLflow's registered Python scorers; it does not register any scorer by itself.

Set `FLEET_JUDGE_MODEL` to the existing MLflow provider/model identifier (for example
`openai:/<service-id>`). Populate the provider environment using
`FleetSettings.judge_environment()` as `fleet eval` does; for an OpenAI-compatible Gateway
this pairs `OPENAI_API_BASE` with `OPENAI_API_KEY`. Keep judge credentials on the server.
Do not substitute the serving or reflection role. Set the same judge environment in the
review/apply client. Importing the scorer makes no judge call and loads no provider client.

The registered v1 body contains only an import and a call with native registration version
1. It invokes the same structured execution diagnostic routine used by the offline suite.
The v2 diagnostic requests a native boolean map from MLflow, with exactly the three
applicability/verdict keys. Its separately structured rationale accompanies both assessments.
This avoids embedding a second JSON document inside a string. Strict boolean types, receipt
citations and the eight-execution evidence limit remain enforced. Recorded v1 JSON responses
remain readable through the original exact five-field validator.
Registration is intentionally limited to a previously absent combined name; a later
registration revision needs its own reviewed native version. Both assessments record code
digest, `execution-evidence-v2`, judge model and target-agent model ID independently. MLflow
retains its evaluation source-run identity; direct validation also records the active run ID.
When no original agent ID exists the target remains `unversioned`. No historical trace is
relabeled as today's agent.

## Review and reconcile experiment 1

Run from the reviewed environment with its explicit tracking destination. These are
operator commands, not instructions to activate live services without approval.

```bash
uv run python scripts/reconcile_mlflow_scorers.py --snapshot /path/to/review.json
```

Check mode performs only registry reads and writes a new local review file. It snapshots
every registered definition, version history, current version, sampling and filter, plus
the proposed import-only definition, local code identity and configured judge identity.
Treat the snapshot/audit as sensitive local operational evidence; do not commit them.

Review the two old names, `sandbox_verification_discipline` and `tool_execution_integrity`:
both must still be version 3 with sample rate 1.0 and no filter. All other registrations
must remain intact. After approval and worker preparation:

```bash
uv run python scripts/reconcile_mlflow_scorers.py \
  --snapshot /path/to/review.json --apply --audit /path/to/new-audit.json
```

Apply compares the complete current snapshot with the reviewed snapshot before any write.
It proves MLflow serialization and worker imports in a new local interpreter, registers
only `execution_evidence_diagnostics`, and checks native version/definition readback.
It then evaluates the saved trace and a copy with its sandbox receipts removed and an
explicit unsupported claim that Python executed and independently verified the answer. The saved
trace may receive positive or negative verdicts; neither is hardcoded. The receipt-free
claim must fail both checks. This normally makes two judge calls in a new evaluation run;
it does not overwrite historical assessments. Results and evaluation/target identities
are retained in the audit. Offline tests separately cover fabricated citations, failed
receipts, placeholders, malformed results and serialization.

Only after validation does apply activate the replacement at 100% with no filter, verify
its schedule, and stop the two old schedules. It checks their identities again before
each stop. Definitions and version histories remain registered. An incomplete audit
must be inspected and recovered at its recorded phase; never rerun apply blindly. MLflow
has no transaction spanning all schedules, so coordinate this operation with other registry
operators. A failure can leave a registered inactive replacement or overlapping schedules.
Verify subsequent automatic assessments' code, rubric, native version, judge and source-run
metadata before declaring the server's worker rollout verified.

When validation failed after registering an inactive v1, preserve that audit and its failed
run ID. After fixing and testing the scorer, create and review a fresh check-mode snapshot,
then use `--resume --snapshot /path/to/recovery-review.json --audit /path/to/new-audit.json`.
Recovery requires the unchanged inactive v1 entrypoint, unchanged old definitions/versions/
schedules and judge destination, and an exact fresh review match. It records the new code
identity, validates without registering again, and saves the evaluation run ID before any
judge call and the saved-trace feedback before the negative control. It then follows the
same activation/readback/stop sequence. A completed Turn is never part of this recovery.
Completed saved-trace judgments are reused only when code, rubric and judge identities match;
their original evaluation-run metadata is retained. A bare numeric answer without receipts
does not necessarily claim tool execution, so it is insufficient as the tool-integrity
negative control.

To restore the old schedules after a completed apply:

```bash
uv run python scripts/reconcile_mlflow_scorers.py --rollback --audit /path/to/new-audit.json
```

Rollback refuses any definition/version/schedule/code-identity drift, reactivates the two
preserved old schedules, verifies them, and stops the combined schedule. Historical feedback
is retained. Do not use rollback as a generic recovery command for an incomplete operation.

## Compare generation caps

`scripts/compare_generation_latency.py` fixes four controlled cases: a direct response,
Flex arithmetic, CSV analysis and RLM with two explicit children. Each arm runs each case
three times in fresh Sessions, followed by one Pi canary: 13 Turns per arm, 39 total.
Gold stays in the helper and is never sent in the task or attached files. Completed traces
receive a code-sourced `review_status=benchmark` expectation to keep them outside curation.

| Arm | Root max tokens | Sub max tokens |
| --- | ---: | ---: |
| baseline | 16384 | provider default (`None`) |
| candidate_a | 8192 | 8192 |
| candidate_b | 4096 | 8192 |

Prepare the plan without contacting services:

```bash
uv run python scripts/compare_generation_latency.py plan --report /path/to/comparison.json
```

For each arm, explicitly configure/restart an authorized comparison server with these
caps and `FLEET_SHARED_NOTES_SUBPATH=''`. Preserve deployments, retries, temperature and
cache policy. No reasoning-effort parameter is introduced. Use the same serving source,
dependencies and loaded program; use separate fixed evaluation tracking destinations as
needed. The client tracking URI must match the API's destination.

```bash
uv run python scripts/compare_generation_latency.py collect --live \
  --arm baseline --base-url http://127.0.0.1:8000 \
  --experiment-id 1 --report /path/to/comparison.json
```

Run the same command for the two candidates against their prepared servers. Each attempt
is journaled before submission, and completed Turn/trace IDs are saved before readback.
An unresolved attempt prevents further collection. On interruption, recover recorded Sessions,
durable results and traces; do not spend additional Turns to replace inconvenient data. After
recovering those rows to measured status, collection skips them and continues only planned rows
in the original evaluation run.
Completed error traces are measured as failed observations, rather than discarded or
replayed. The comparison's readback explicitly accepts those traces; ordinary evaluation
readback still requires success. Sessions are evicted after measurement, retaining their
durable results.
Readback waits for the native model ID as well as completed spans: MLflow can publish
association metadata later. The native ID must match the original Turn-span identity.
If readback times out, refresh the saved trace; never substitute a current model ID.

The report separates end-to-end latency, model-span latency, token receipts, observable
generation calls, logical budget units/breakdown, parser errors/recoveries, truncation,
correctness and isolation. It records native agent identities and one MLflow evaluation
run ID per arm. Model-call time is a sum of leaf spans and can exceed wall time with
parallel children. Observable calls count DSPy model spans; internal provider retries
are not claimed as separately measured wire requests.

Each new attempt records the controlled-configuration digest, selected arm caps and the
checked effective configuration digest before submission. The trace configuration must
match that checked digest. Final acceptance requires retained controls and consistent
configuration/fingerprint/native model identity within each arm and mode. Native agent
IDs may differ across arms and modes because those settings are part of agent identity.
Older observations without these receipts remain incomplete evidence; do not invent
receipts to make an interrupted comparison eligible.

The pinned DSPy legacy callback may omit provider finish reasons and cached usage receipts.
Missing telemetry stays unknown. Do not infer non-truncation from success or replace
unknown tokens with zero. Obtain authoritative provider finish-reason evidence before
accepting such a comparison; the tool deliberately blocks promotion without it.

```bash
uv run python scripts/compare_generation_latency.py compare --report /path/to/comparison.json
```

Promotion requires complete valid version association, fresh isolated Sessions, correct
answers (including Pi), no truncation, no aggregate generation-call increase, and at least
10% lower median latency across the 12 short observations. An unsuccessful candidate does
not disqualify another candidate. The tool reports eligibility only: it never installs a
configuration or changes serving defaults. Record actual agent/evaluation/Turn/trace IDs
and results after the authorized run; offline tests establish no measured latency gain.

## Experiment 1 closeout

The authorized comparison `latency-7a163484f74f` completed all 39 Turns. All three Pi
canaries passed. The baseline included one cancelled child-delegation Turn; its latency
and failed outcome remain in the evidence. No completed Turn was replayed.

| Arm | Correct Turns | Median short-task seconds | Observable generation calls | Evaluation run |
| --- | ---: | ---: | ---: | --- |
| Baseline | 12/13 | 58.69 | 66 | `0049d957b9e546449b9124e874aa2eb3` |
| Candidate A | 13/13 | 42.07 | 60 | `7607f2ee0ad64354adcc324fa3573b33` |
| Candidate B | 13/13 | 38.38 | 67 | `71e1a2cd51a74b6d8b8494b06b588a03` |

These are descriptive observations; neither candidate qualifies for promotion. Provider
finish reasons are unavailable for all 39 Turns, complete token receipts exist for only
five baseline Turns, and older baseline observations lack the subsequently added control
receipts. Candidate B also increased observable generation calls. Unknown telemetry is
not zero usage or evidence of non-truncation. Original serving defaults were restored.

All 39 saved traces have native model IDs matching their original Turn identities.
The original trace remains unversioned with its 68 spans and five historical assessments
unchanged. Per-arm metrics and identity manifests are stored under `generation-latency`
in the evaluation runs above; local operational evidence is retained in
`.scratch/assessment-activation-20261029/`.

The combined scorer remains registered as inactive version 1. Its saved-trace verdicts
were positive and its unsupported-execution negative control failed both checks; the audit
is stored under `assessment-rollout` in run `f5c5c2bbf6c24bfcb257faaabb6a7784`.
Native automatic activation is blocked by MLflow 3.17.2's gateway-model eligibility rule.
The two old version-3 schedules remain active at 100% without filters, and all other
registrations are preserved. A replacement worker or platform change is deferred.
