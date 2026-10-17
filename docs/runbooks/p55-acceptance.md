# P5.5 governed acceptance

P5.5 follows the [Databricks quality loop](https://docs.databricks.com/gcp/en/mlflow3/genai/agent-observability-and-quality).
A fully evaluated, rejected candidate is a valid result. Missing data, failed
calibration, unresolved execution, unconfirmed cleanup or unreconciled costs
leave acceptance open. Installation remains a separate explicit action.

## Publish scorers first

Use the credential resolution in the [evaluation canary runbook](evaluation-canary.md).
Confirm rotation of the token from the historical stale-judge-URL incident.

```bash
uv run fleet eval scorers plan
uv run --env-file .env fleet eval scorers register
uv run --env-file .env fleet eval scorers verify
```

`plan` performs no remote writes. Registration requires existing evaluation and
optimization experiments with their exact configured UC trace locations. Scorers
belong to experiments; UC stores associated traces and datasets. Publication
reads back pinned versions into `.fleet/compiled/scorers.lock.json`, reuses
identical definitions and refuses to overwrite active production scorers.
Verification detects definition drift. Monitoring stays disabled.

Built-in registered judges use the Databricks-native URI of the same configured
service. Fleet's judge retains its DSPy Gateway route. Credentials and live LMs
are never serialized. Custom scorers import the installed Fleet package in the
evaluation client. Compatibility failures block publication rather than select
another model. DSPy 3.4.1 and MLflow 3.17.1 remain pinned.

## Review and freeze data

Require eight approved selected replays across three source groups. Two groups
remain noncritical for train/dev; critical groups go entirely to holdout. Review
cross-Session source lineage and answer-bearing history/memory. Use complete gold
answers. Required output paths must be absent from the immutable replay snapshot;
curate clean cases rather than editing replay bytes. Synthetic status alone does
not authorize training. This is an engineering pilot, not a statistical benchmark.

```json
{
  "schema_version": 1,
  "artifacts": [
    {"path": "artifacts/totals.json", "kind": "json", "expected": {"North": 200, "South": 240}},
    {"path": "artifacts/report.py", "kind": "python", "execute": true, "stdout": "440\n"}
  ],
  "children": [{"input_paths": ["files/sales.csv"], "artifacts": ["totals.json"]}]
}
```

```bash
uv run --env-file .env fleet eval label TRACE_ID --answer "North 200; South 240" \
  --value 200 --value 240 --route flex --expectations-file expectations.json
uv run --env-file .env fleet eval dataset --reviewed --session SESSION
```

Omitting the expectations file preserves labels; an object replaces them; `{}`
clears them. Migration 3 adds JSONB storage. Gold stays outside program inputs.
Frozen dataset names include their digest under `uscentral.fleet_evaluation`;
Lakebase remains authoritative for replay bytes.

Validators support exact UTF-8 text, JSON field/value subsets and CSV rows matched
by `key_columns`. JSON may have extra fields; lists and CSV row-key sets must match.
Numeric checks are exact unless `tolerance` declares an absolute difference.
Unsafe/duplicate paths and unknown validators are rejected. Python requires a
`.py` path and `execute=true`: a fixed runner executes it in Daytona within at
most 60 seconds and the remaining Turn deadline. No generated code runs locally.
Check stdout and declared output files separately.

Collectors observe up to 32 artifacts, 64 KB each, 256 KB total. Incomplete
required evidence fails quality. Child checks use copied paths, invocation success
and promoted hashes across explicit/native lifecycles. Encoded artifact bytes are
decoded before trace redaction; altered bytes cannot satisfy the original digest.

## Calibrate and forecast

Review [the twelve draft controls](../../config/acceptance-controls.json), mark
`review_status` as `approved`, and supply the actual reviewer identity. Six valid
and six negative answers cover incorrect values, swapped associations, unsupported
claims and unsafe content. All twelve correctness/safety verdicts must match.
Controls stay separate from GEPA splits. Calibration disables caching and binds
control/scorer digests. Failure blocks GEPA without automatically training a judge.

Copy [the ledger template](../../config/acceptance-ledger.example.json) into ignored
local evidence. Fill every rate using reconciled usage and conservative assumptions;
null rates deliberately fail. `rollout_usd` includes serving, retries, judging,
sandbox lifecycle and artifact verification per full rollout. `reflection_usd`
adds search proposal cost, `judge_usd` covers a control, and `fixed_remaining_usd`
covers warehouse/fixed overhead. Unknown costs are not zero.

```bash
uv run --env-file .env fleet eval calibrate --controls config/acceptance-controls.json \
  --acceptance-ledger .scratch/p55/ledger.json --output .fleet/compiled/calibration.json
uv run --env-file .env fleet optimize --session SESSION --threads 1 \
  --max-metric-calls 6 --acceptance-ledger .scratch/p55/ledger.json --preflight
```

Three paired validation passes require `6 × (dev examples + holdout examples)`
rollouts after search. Keep the $60 cumulative cap, $48 admission threshold and
$12 reserve. Reservations survive failures. These are forecast controls, not
provider-enforced dollar limits. Overlapping billing observations supersede earlier
observations; do not add them as new charges.

## Execute, recover, and record

```bash
uv run --env-file .env fleet optimize --session SESSION --threads 1 \
  --max-metric-calls 6 --acceptance-ledger .scratch/p55/ledger.json
uv run --env-file .env fleet optimize --session SESSION --threads 1 \
  --max-metric-calls 6 --acceptance-ledger .scratch/p55/ledger.json --resume CAMPAIGN
```

Stable IDs, an exclusive lock, provider identifiers, the saved winner and individual
receipts live under `.fleet/compiled/runs/<campaign>/`. Resume checks code,
dependencies, incumbent, configuration, labels, dataset, calibration and registry.
It reuses completed execution and resumes scoring from saved predictions.
Unresolved started execution/search requires inspecting saved provider IDs/logs;
it cannot automatically replay. Deployment/Session settings apply; paired
validation disables serving response caches only.

The production campaign records its MLflow run, UC dataset, candidate/manifest,
numeric usage, cleanup and acceptance trace readback. The synthetic
`scripts/verify_gepa_live.py` canary intentionally does not export to MLflow and
cannot replace production evidence.

Preserve historical route scores. The rubric is direct for no execution, Flex for
bounded computations/files, and RLM for exploration. New reviewed labels supersede
older expectations without erasing the conversion mismatch.

Close P5.5 after scoring governance, review, calibration, complete paired evaluation,
artifact/delegation evidence, cleanup and final cumulative cost reconciliation.
Record candidate eligibility independently. Keep Lakebase lease-loss fault injection
and other unexercised operational checks explicitly open.
