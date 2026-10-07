# Fleet MLflow evaluation pilot

Use the existing supervised Fleet CLI and local MLflow 3.16.1. This workflow uses
Fleet's public Session/Turn API and native `mlflow.genai.evaluate()`; it does not
create another reasoning runtime. Provider and Daytona runs require operator
authorization. Keep unrelated configuration and checkout edits intact.

## Commands

Run from the repository root with the configured `.env` available. Credentials
remain in process memory; never print `.env` or place credentials in artifacts.

```bash
uv run python scripts/mlflow_evaluation.py inventory      # read-only
uv run python scripts/mlflow_evaluation.py backup
uv run python scripts/mlflow_evaluation.py reset          # preview
uv run python scripts/mlflow_evaluation.py reset --apply
uv run python scripts/mlflow_evaluation.py validate-judge
uv run python scripts/mlflow_evaluation.py register
uv run python scripts/mlflow_evaluation.py datasets
uv run python scripts/mlflow_evaluation.py dry-run
uv run python scripts/mlflow_evaluation.py backtest
uv run python scripts/mlflow_evaluation.py pilot          # preview
uv run python scripts/mlflow_evaluation.py pilot --apply
uv run python scripts/mlflow_evaluation.py recall          # preview only if recall was deferred
uv run python scripts/mlflow_evaluation.py recall --apply
uv run python scripts/mlflow_evaluation.py review
uv run python scripts/mlflow_evaluation.py finalize       # finalized trace evidence
uv run python scripts/mlflow_evaluation.py report         # existing assessments
```

The campaign output is under ignored `.fleet_rlm/mlflow/evaluation/`. `--directory`
selects the backup/reset source. A pilot receipt prevents accidental repeat
submissions. The recall command completes only a deferred fifth Turn within the
original deadline; it refuses repeated recall or an unfinished 8K task. One pilot submits at most five Turns, stops submissions at minute 55,
and requests cancellation at minute 60. It does not retry failed Fleet tasks or
change runtime policy. Inspect `pilot-active.json`, per-turn SSE event receipts,
committed history, trace IDs, and cleanup evidence. Leave services running.

## Backup and recovery

The reset verifies SHA-256 checksums before removing registered judge versions,
soft-deleting evaluation runs, and deleting the two legacy datasets. Raw traces,
assessments, sessions, Fleet's database, and the registered signature prompt are
preserved. The manifest lists original IDs and all exported configurations and
versions; artifact downloads and full metric histories are included.

```bash
uv run python scripts/mlflow_evaluation.py restore          # preview
uv run python scripts/mlflow_evaluation.py restore --apply
```

Run recovery in an isolated destination or resolve judge-name conflicts first.
Recovery refuses to overwrite judges. Judge definitions use their original raw
wire payload because some historical definitions cannot be deserialized by the
installed scorer validator. Runs retain their IDs; recreated datasets receive new
IDs and the `-restored` suffix. Judge versions retain ordering but new registry
identities may differ. Original invalid definitions may still be invalid.

## Dataset and agent provenance

Three native datasets contain core expectations, OOLONG standard/guided variants,
and trace-linked regressions. Freeze each campaign using its native dataset ID
and persisted digest: this server returns no separate numerical dataset version.
The script validates those digests before evaluation and logs native dataset
inputs, the frozen manifest, and source snapshots. Do not mutate datasets used by
an existing campaign; create a new suite version instead.

OOLONG records retain the source revision, row ID/index, input hash, and a grouping
key. Reference labels and answers are used only for scoring, never submitted to
Fleet. Keep examples from the same context group together in future calibration
and held-out splits. This small guided trial is not an official benchmark score.

The logged agent identity records checkout SHA, dirty-diff/configuration hashes,
dependency versions, judge route, and prompt-registry reference. Run artifacts
identify the scorer source hash. Fleet currently does not consume a registered
prompt-version URI: the registry link is a provenance reference, not proof that
the runtime used that exact template. Model routes observed in execution traces
are authoritative; environment declarations and TOML defaults can differ.

## Five checks and local-server limits

`answer_correctness` uses exact withheld answers for OOLONG and registered builtin
Correctness for prose. `evidence_support` selects successful tool evidence in code
and uses registered Guidelines. The same validated Unity Gateway model route is
used for both semantic judges. Provider failures and scorer timeouts are errors,
not passes.

The other three checks are deterministic code scorers: required execution,
confirmed child cleanup, and settlement/commit reconciled with committed public
history. OSS MLflow 3.16.1 explicitly forbids registering arbitrary code scorers.
They run inline in native evaluation and appear as per-record assessments and
metrics; only the two supported semantic definitions appear in Judges. Do not
bypass that security restriction or substitute LLM guesses for code checks.

Aggregation needs successful root Python count output, appropriate arithmetic
code, and the expected total. Child results alone do not prove root aggregation.
No acquired child is explicitly not applicable to cleanup. Missing traces or
durable state are errors. Open traces can outlive cancelled work while workers
drain; `IN_PROGRESS` alone does not prove failure. Truncated inputs are invalid
cases, not model-quality failures. Hydration alone is not completed recall.

Each evaluation report includes scored/applicable/error/not-applicable/invalid
counts alongside pass rates. Historical assessments retain their original run
provenance; filter assessment metadata by the new run ID before comparing scores.

## Human calibration and activation

The native review queue uses independent `fleet-v1-human-*` feedback schemas with
comments. Humans must submit their own decisions; automated scores never populate
human feedback. Queue disagreements, failures, missing evidence, and representative
passes for calibration. Unfinished historical traces may lack a root input/output;
use the full trace and exported committed-history evidence when reviewing them.

Keep all registered scorers inactive (zero sampling) during the pilot. Activation
requires human-reviewed agreement on representative positive/negative cases,
acceptable judge latency/error rates, reliable execution evidence, and a held-out
split without related-context leakage. Enable only eligible settled execution
traces: root `fleet_turn`, user answer present, valid submitted input, durable
commit reconciled, and execution provenance available. Exclude preparation,
judging, invalid submission, and cancellation traces from answer monitoring.
Broader campaigns and optimization follow calibration. Report tokens and latency;
report cost only when verified pricing and complete usage are available.

## Gateway monitoring controls

The dedicated `fleet-evaluation-deepseek-v1` endpoint uses the pilot's Databricks
Unity model over OpenAI-compatible transport. Its credential is held in MLflow's
encrypted secret store; setup requires an existing non-default passphrase. It
does not replace unrelated routes, secrets, or Fleet configuration. Server-side
validation uses a small MLflow-only fixture and the native asynchronous judge
worker, without a Fleet Turn or historical assessment backfill. Successful
validation registers gateway-backed versions of both semantic judges; prior
versions remain available.

```bash
uv run python scripts/mlflow_evaluation.py monitoring-setup           # preview
uv run python scripts/mlflow_evaluation.py monitoring-setup --apply
uv run python scripts/mlflow_evaluation.py monitoring-validate
uv run python scripts/mlflow_evaluation.py monitoring-ai-review       # preview
uv run python scripts/mlflow_evaluation.py monitoring-ai-review --apply
uv run python scripts/mlflow_evaluation.py monitoring-calibrate      # read-only gate status
uv run python scripts/mlflow_evaluation.py monitoring-status
uv run python scripts/mlflow_evaluation.py monitoring-report
uv run python scripts/mlflow_evaluation.py monitoring-activate        # preview
uv run python scripts/mlflow_evaluation.py monitoring-activate --apply
uv run python scripts/mlflow_evaluation.py monitoring-stop            # preview
uv run python scripts/mlflow_evaluation.py monitoring-stop --apply
```

Monitoring artifacts live in the campaign's `monitoring/` directory. Setup and
successful validation are idempotent; repeated validation reads the existing
worker result without another provider call. An unsuccessful worker invocation
is retained for diagnosis, not automatically retried. Stop is idempotent and
does not depend on calibration services. It disables only these two judges.

AI review is explicitly identified as `LLM_JUDGE`, with separate
`fleet-v1-ai-review-*` assessment names. It does not populate the
`fleet-v1-human-*` review fields or mark queue items completed. Programmatic
expectations that use MLflow's default HUMAN source are also not independent
human calibration. Independent human review and a native comparison against
the new gateway scorer versions remain necessary.

## Provisional AI-reviewed monitoring

The operator explicitly authorized the ten AI-assisted browser reviews as a
provisional calibration baseline on 2026-10-08. This overrides the human-review
gate for this campaign only; it does not make those reviews independent human
feedback. Successful native worker responses, agreement on the reviewed cases,
and no false passes on unavailable evidence are still required.

`scripts/mlflow_monitoring.py` constructs native `InstructionsJudge` candidates
that can inspect execution spans with MLflow trace tools. Candidate definitions
are not registered or activated until their native server calibration passes.
Calibration invokes the ten saved traces without logging new historical
assessments. Dispatch receipts prevent duplicate paid jobs after uncertain
outcomes. Placeholder rationales, scorer errors, missing results, and differences
from the provisional baseline block activation.

Activation hashes the candidate definitions used by that attempt and compares
the complete serialized definition with the registered scorer, including its
model, output type, inference parameters, and rationale mode. It reuses an exact
match or registers the calibrated candidate as a new version. Before writing an
activation receipt, it reads the scorer back and verifies the definition,
registered version, sample rate, and eligibility filter.

```bash
uv run python scripts/mlflow_monitoring.py calibrate           # paid native calibration; idempotent
uv run python scripts/mlflow_monitoring.py calibration-status
uv run python scripts/mlflow_monitoring.py activate            # preview; fail closed
uv run python scripts/mlflow_monitoring.py activate --apply    # qualifying judges only, at 50%
uv run python scripts/mlflow_monitoring.py status
uv run python scripts/mlflow_monitoring.py stop --apply        # idempotent, only these two judges
```

The candidate eligibility checker requires a future completed root `fleet_turn`,
valid input and final answer, successful settlement and commit spans, and a
matching committed transcript fetched through Fleet's public API. It excludes
preparation, evaluation-generated, cancelled, incomplete, and malformed OOLONG
submissions. After successful activation, run `eligibility` to preview tagging,
`eligibility --apply` for one pass, or `watch --apply` for ongoing reconciliation.
The watch command refuses to start without an activation receipt. Only MLflow's
native worker performs sampling and judging. No Fleet runtime policy or public
API changes are introduced.

The native filter combines the activation timestamp, execution phase, root name,
status OK, and the `fleet.online_evaluation_eligible=true` attestation. Sampling
applies independently to each judge. MLflow's installed native worker uses a
five-minute trace-completion buffer based on trace start time; late completion
or late attestation can therefore miss its checkpoint window. Resolve and test
that limitation before claiming comprehensive monitoring coverage.

The initial trace-aware calibration did **not** qualify either judge:
`answer_correctness` disagreed on seven of ten reviewed cases and
`evidence_support` disagreed on six. Results included placeholder rationales,
unrelated task descriptions, and a scorer timeout. An additional smoke test
false-passed an invalid cancelled submission. See campaign artifacts
`monitoring/provisional-monitoring.json`, `native-calibration-*.json`, and
`trace-judge-*-result.json` for native job IDs and original responses.
The gateway remains configured and old scorer versions are preserved; sampling
remains zero until a candidate actually passes calibration.

Transport probes confirmed that text mode can inspect real execution spans, but
its final output can fail native JSON parsing. JSON-object mode also failed:
root-context invocation was rejected upstream and trace-only invocation returned
empty/malformed assessments. These candidates remain unregistered.

Fleet's 10,000-character root input preview truncates the valid 20,383-character
8K pilot input. Eligibility uses the complete committed public-history request,
bound to the same trace/Run and preceding user sequence; it does not classify a
truncated trace preview as an invalid submitted task. The native judge's access
to complete input remains an observability gap. No trace policy changes or
historical eligibility tags were applied.
