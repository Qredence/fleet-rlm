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
not a substitute for `make check-release` or reproducible-build provenance.

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
window. A rollback pair requires distinct revisions and the same database head.
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
There is no complete promotion decision or service-switch command yet. Do not
interpret successful `prepare` or `validate-rollback-pair` as a passed gate.

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

Warm capacity is deferred for the current 0.7.8 continuation by operator
instruction; this neither changes configured policy nor certifies a warm pool.
The [Phase 6 task list](../../fleet-rlm-implementation-plan-2026-09-06-v2.md#phase-6---promotion-rollback-and-final-deletion)
tracks remaining work. Historical runtime/adapter v2 benchmark receipts remain
readable without rewriting their seals; new receipts use v3, and comparisons
cannot mix generations. These scripted lanes do not prove live quality.
