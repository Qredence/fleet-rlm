# Phase 6 promotion and rollback

Fleet promotes a complete, immutable release bundle. The bundle binds one clean
checkout, built artifact manifest, resolved non-secret policy, dependency lock,
Session and SemanticChild image identities, database head, and quality dataset
and scorer identities. It does not contain credentials, infrastructure errors,
or user content.

Build artifacts first, then create a write-once bundle receipt outside tracked
source files:

```bash
make check
make check-security
make build-release
uv run python scripts/phase6_promotion.py prepare \
  --profile daytona-recursive \
  --artifact-manifest dist/artifact-manifest.json \
  --images .fleet-evidence/phase6/images.json \
  --database-head <alembic-head> \
  --dataset-digest <sha256> \
  --scorer-digest <sha256> \
  --output .fleet-evidence/receipts/phase6/candidate.json
```

The candidate checkout must be clean throughout capture. The v2 bundle verifies
the artifact manifest against wheel/sdist bytes and matches the wheel's Python
sources against tracked candidate sources. It validates the selected profile
and hashes its merged, unresolved policy without reading credentials. This is
not a substitute for `make check-release` or live provenance. `make build`
derives `SOURCE_DATE_EPOCH` from the candidate commit and normalizes wheel and
sdist metadata before writing the manifest, so two clean builds of one SHA must
produce identical artifact bytes.

`images.json` binds both snapshot identifiers to manifest and probe receipt
SHA-256 values (replace these illustrative hashes with actual receipt hashes):

```json
{
  "session": {
    "snapshot": "fleet-rlm-python313-v10",
    "manifest_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
    "probe_sha256": "2222222222222222222222222222222222222222222222222222222222222222"
  },
  "semantic_child": {
    "snapshot": "fleet-rlm-python313-child-v5",
    "manifest_sha256": "3333333333333333333333333333333333333333333333333333333333333333",
    "probe_sha256": "4444444444444444444444444444444444444444444444444444444444444444"
  }
}
```

Create and validate an equivalent baseline bundle before opening the maintenance
window. Run the same v2 builder from the baseline checkout with
`FLEET_PHASE6_ROOT=/path/to/clean-baseline` when the released tag predates the
helper script; this keeps the baseline source and `HEAD` checks scoped to that
isolated worktree. A rollback pair requires distinct revisions and the same database head.
This checks identity only: equal heads do not prove additive compatibility with
data written by the other release. The receipt always reports
`switch_eligible: false` and names the missing compatibility, quiescence, and
durable-continuity evidence:

```bash
uv run python scripts/phase6_promotion.py validate-rollback-pair \
  --baseline .fleet-evidence/receipts/phase6/baseline.json \
  --candidate .fleet-evidence/receipts/phase6/candidate.json \
  --output .fleet-evidence/receipts/phase6/rollback-pair.json
```

During the maintenance window, close new admissions, settle or cancel and fence
active Runs, and verify provider cleanup before switching the complete bundle.
Rehearse baseline → candidate → baseline → candidate. At each stage verify
existing Session history, workspace files, artifacts, and a new public API/SSE
Turn. Do not restore an older database snapshot over newer user data.

The receipt validates identity only. It does not authorize provider activity,
warm-pool creation, service restart, package publication, or promotion. Record
those separately in the ADR 006 evidence ledger.

Image, dataset, and scorer hashes are supplied identity claims, not validation
of the referenced evidence. A self-hash detects modification, not authenticity.
The repository now has write-once campaign, rehearsal, deletion-inventory, and
promotion-decision readers. They remain evidence contracts, not deployment
authority: a decision is eligible only when every gate is backed by validated
live receipts. Do not interpret successful `prepare` or `validate-rollback-pair`
as a passed gate.

When a live prerequisite is unavailable, seal an honest blocked decision rather
than omitting the decision or inventing a receipt. Repeat `--blocker` for every
false gate; the command recomputes the list and refuses an inexact one. Missing
receipt identities remain `null` and the resulting v1 decision is permanently
ineligible:

```bash
uv run python scripts/phase6_promotion.py seal-blocked-decision \
  --baseline .fleet-evidence/receipts/phase6/baseline.json \
  --candidate .fleet-evidence/receipts/phase6/candidate.json \
  --blocker strict_daytona \
  --blocker trusted_scorer \
  --blocker campaign_complete \
  --blocker quality_noninferior \
  --blocker latency_within_tolerance \
  --blocker cost_within_tolerance \
  --blocker database_compatibility \
  --blocker rollback_rehearsal \
  --blocker quiescent \
  --blocker deletion_inventory \
  --clean-candidate-verified \
  --output .fleet-evidence/receipts/phase6/promotion-decision-blocked.json
```

The blocked command validates any optional pair, preflight, campaign,
rehearsal, strict-proof, and deletion receipts before carrying their digests;
it never turns incomplete evidence into a passing gate.

## Offline preflight and measurement comparison

`validate-switch-preflight --baseline BASELINE --candidate CANDIDATE
--observation OBSERVATION --output RECEIPT` checks a recent maintenance
controller observation. Its `fleet.phase6-switch-observation/v1` object must
contain exactly: both `baseline_bundle_sha256` and `candidate_bundle_sha256`,
timezone-aware `observed_at`, `database_head`, `database_compatibility_sha256`,
`admissions_closed: true`, `provider_cleanup_confirmed: true`, and integer-zero
`active_runs`, `active_workers`, and `pending_cleanup` (plus `schema`). It rejects
observations older than 60 seconds or from the future. The controller must hold
the admission fence through the switch; an offline observation cannot prove
that the state remains quiescent. The compatibility digest is a reference to
evidence the operator still must verify, not compatibility proof itself.

`compare-quality --baseline BASELINE --candidate CANDIDATE
--baseline-measurements BEFORE --candidate-measurements AFTER --output RECEIPT`
checks `fleet.phase6-quality-measurements/v1` objects containing exactly:

- `bundle_sha256`, `execution_mode: "live"`, and `complete: true`;
- `samples`: matched `case_id` / zero-based `repetition` pairs with `score`
  between zero and one, finite nonnegative `seconds`, and `cost_usd`;
- `receipt_sha256`: SHA-256 of compact, key-sorted JSON excluding that field;
- `schema`.

At least two complete repetitions are required. The reader recomputes mean
quality, nearest-rank p95 latency, and total cost: quality may not decline,
and latency/cost may increase by at most 10%. A regression returns exit code 1
while retaining the write-once result. Unknown costs are not treated as zero.
These are measurement-reader contracts, not new live campaign producers.
Dataset coverage and trustworthy live provenance still need independent
validation; a passing comparison always reports `promotion_eligible: false`.

Host-produced rows can be sealed without rewriting unknown values:

```bash
uv run python scripts/phase6_promotion.py seal-measurements \
  --bundle-sha256 <bundle-sha256> --samples samples.json \
  --output .fleet-evidence/receipts/phase6/measurements.json
```

`samples.json` must contain a `samples` list with matched case/repetition rows
and explicit finite `cost_usd` values. This command seals observations; it does
not claim that a model or scorer actually ran.

## Trusted GEPA and live strict proof

The host-owned `TrustedGEPAFeedbackMetric` in
`src/fleet_rlm/optimization/metric.py` returns DSPy
`Prediction(score=..., feedback=...)`. It rejects unbounded or sensitive
feedback, keeps qualitative-only expectations unscorable, and never uses an
observed answer as ground truth. `run_authoritative_gepa` in
`src/fleet_rlm/optimization/gepa_runner.py` requires a validated
`fleet.strict-daytona-proof/v2` receipt, distinct task/reflection models,
`auto=None`, one explicit `max_metric_calls` budget (the 8+24-round budget),
`track_stats=True`, held-out evaluation, and a fresh-process instruction reload.
It persists only bounded GEPA statistics and hashes, not candidate traces or raw
task content.

The opt-in production-boundary proof is:

```bash
FLEET_LIVE=1 RUN_LIVE_DAYTONA_STRICT_PROOF=1 \
  FLEET_STRICT_DAYTONA_PROOF_ROOT="$PWD/.fleet-evidence/receipts/phase6" \
  uv run pytest tests/live/backend/test_strict_gepa_daytona_proof.py -x -q
```

It uses the host-polled authenticated retained broker with Daytona
`network_block_all=true`, no volume, no public gateway/tunnel, no outbound
allow-list, and zero provider auto-delete interval. A skipped, incomplete, or
failed run writes no proof. The resulting receipt is an evaluator admission
precondition, not a quality or promotion result.

## Fenced maintenance and deletion inventory

`src/fleet_rlm/optimization/maintenance.py` defines the adapter seam for the
real shared admission fence. The controller closes admissions, settles/fences
Runs and workers, confirms cleanup, re-reads quiescence while holding the fence,
switches the complete bundle atomically, verifies durable continuity and stage
health, then releases the fence. A failure keeps the fence held. A deployment
composition must provide the real cross-process adapter; the controller does
not substitute an in-process lock.

After the exact baseline → candidate → baseline → candidate rehearsal, seal an
explicit deletion inventory. If no migration-only path is proven safe, use an
empty candidate list; the receipt records a deliberate `no-op` and retains
broker execution, generation/fencing, cleanup ownership, progress fingerprints,
historical readers, and compatibility parsers.

```bash
uv run python scripts/phase6_promotion.py seal-deletion-inventory \
  --rehearsal-sha256 <rehearsal-sha256> --candidates candidates.json \
  --checks deterministic-checks.json \
  --output .fleet-evidence/receipts/phase6/deletion-inventory.json
```

## Curated local MLflow input

Use `scripts/benchmarks/curate_mlflow.py capture --trace-id ID --output SOURCE`
with explicitly selected trace IDs. Capture reads only a loopback MLflow server,
extracts root requests (never answers or reasoning), hashes source/Session
identities, and rejects suppressed, redacted, oversized, or visibly truncated
requests. `--base-snapshot SOURCE` can extend a sealed inventory into a new file.

Review each full request before building a `fleet.phase6-curation-review/v1`
document. Each reviewed entry binds `record_id` and `source_query_sha256`, records
`review_status: "agent_reviewed"` and `self_contained: true`, and provides
`task_family`, `task_origin`, nonempty `expectations.criteria`, `output_contract`,
and `execution_requirements`. The review also binds `source_snapshot_sha256`
and explicitly sets `reviewer: "agent"`. Do not call agent-drafted expectations
human labels or derive expected answers from the observed model response.

```bash
uv run python scripts/benchmarks/curate_mlflow.py export \
  --snapshot .scratch/phase6-curation-20260914/source-v2.json \
  --review .scratch/phase6-curation-20260914/review.json \
  --output .scratch/phase6-curation-20260914/export-new.json
```

Export checks source seals and reviewed query identities, rejects duplicate
tasks, then uses the existing dataset validator and grouped splitter with seed
42. It preserves known project groups and otherwise groups related task families.
The write-once export contains the split manifest and draft provenance. Keep
these task payloads outside tracked source and expose only the public manifest
to campaign reporting. A structurally valid draft remains non-promotable until
trusted scoring, capability coverage, strict evaluator proof, and held-out
evaluation pass. Do not silently remove authorization or infrastructure controls
to make an old task executable under the new runtime.

For a human-aligned artifact, use `export-human` with
`fleet.phase6-curation-review/v2`, an opaque reviewer ID, and an explicit
approved/corrected decision for every source record. The agent-reviewed export
remains a draft and stays non-promotable; observed model answers are never
ground truth.

Warm capacity is deferred for the current 0.7.8 continuation by operator
instruction; this neither changes configured policy nor certifies a warm pool.
The [Phase 6 task list](../../fleet-rlm-implementation-plan-2026-09-06-v2.md#phase-6---promotion-rollback-and-final-deletion)
tracks remaining work. Historical runtime/adapter v2 benchmark receipts remain
readable without rewriting their seals; new receipts use v3, and comparisons
cannot mix generations. These scripted lanes do not prove live quality.
