# Maintained scripts

| Script | Purpose |
| --- | --- |
| `benchmarks/runtime_v2.py` | Execute repeated scripted Turns and compare sealed lifecycle migration receipts; no live semantic or Daytona guarantee |
| `db_init.py` | Apply the Alembic chain to `FLEET_DATABASE_URL`; keep it aligned with the runtime policy target |
| `migrate_sqlite_to_postgres.py` | Operator-gated one-time SQLite-to-managed-PostgreSQL import with verified backup and content-free receipt |
| `openapi_tools.py` | Generate or check backend-only `openapi.yaml` |
| `generate_stream_fixture.py` | Generate or check the deterministic TUI turn-stream golden fixture |
| `generate_tui_chunk_validation.py` | Generate or check the TUI runtime chunk-validation tables from `openapi.yaml` |
| `generate_profile_matrix.py` | Generate or check the provider/profile matrix from `config/fleet.toml` |
| `check_codebase_tree.py` | Enforce canonical import and route boundaries |
| `check_dependency_boundaries.py` | Check P50 domain dependency directions and Daytona Memory-policy residue |
| `check_harness_engineering.py` | Validate repository guidance and harness contracts |
| `check_docs_quality.py` | Validate documentation structure and links |
| `check_agents_md_freshness.py` | Validate agent-guide reachability |
| `validate_release.py` | Validate package metadata and wheel contents |
| `live_phase1_stream_verify.py` | Run the narrow one-Turn Phase 1 native DSPy stream canary on the normal Daytona profile |
| `live_phase2_recursive_verify.py` | Run the narrow Phase 2 dedicated-child native DSPy canary on `daytona-recursive` |
| `live_daytona_verify.py` | Run the opt-in Daytona MVP proof and validate its bounded JSON receipt |
| `release_smoke.py` | Smoke-test installed wheel bytes, bundled assets, CLI entry points, and OpenAPI without provider startup |
| `benchmark_daytona_lifecycle.py` | Benchmark full Daytona create-through-first-execution lifecycle and select retained versus per-Turn mode |
| `benchmarks/corpus_chain.py` | Deterministic corpus-chain benchmark fixtures and report validation |
| `benchmarks/run_native_long_context.py` | Measure native whole-value URL context at 1/5/10 MiB and emit the paging decision receipt |
| `benchmarks/run_rlm_latency.py` | Compare live Fleet RLM configuration variants and run the MLflow-native five-task quality gate |
| `benchmarks/attach_phase3_receipt.py` | Attach a validated, bounded Daytona native-feasibility receipt and capability metrics to an existing MLflow campaign run |
| `benchmarks/record_mlflow_campaign.py` | Record one sealed runtime/adapter benchmark receipt as an explicit MLflow tracking run with identity params, full-run metrics, and evidence-lane tags |
| `benchmarks/run_routing_eval.py` | Run the deterministic or opt-in live delegation-ladder benchmark, including bounded recursive batches |
| `benchmarks/judges.py` | Shared Fleet evaluation judge definitions and registration |
| `benchmarks/scorers.py` | MLflow 3 GenAI custom scorers and evaluation metric definitions |
| `benchmarks/manage_prompts.py` | Manage and version Fleet signature prompts in MLflow Prompt Registry |
| `benchmarks/annotate_traces.py` | Annotate persisted `fleet_turn` traces with derived aggregate attributes |
| `daytona_snapshot.py` | Explicitly create or check the immutable Fleet Daytona Snapshot |
| `daytona_warm_pool.py` | Plan, inspect, or explicitly reconcile the clean SemanticChild Daytona warm pool |
| `inventory_db_heads.py` | Retain a read-only, content-free Alembic-head inventory for one named database target |
| `codex_feedback_loop.py` | Run the local Codex feedback-loop probes |
| `deployment_observability.py` | Inspect deployment observability inputs |
| `circleci_trigger_release.py` | Trigger and await the GitHub Actions PyPI release from CircleCI |
| `validate_mlflow_tracing.py` | Emit and validate a local or Managed Databricks trace using the selected Fleet TOML policy |
| `benchmarks/certify_mlflow.py` | Run the bounded MLflow 3.16 certification lane with explicit local/configured backend selection and a write-once receipt |
| `benchmarks/certify_postgres.py` | Certify contention and optional query plans against an explicitly designated exclusive test database |
| `benchmarks/certify_daytona_sdk.py` | Retain Daytona 0.210.0 unit compatibility evidence while explicitly preserving unrun live surfaces |
| `benchmarks/rlm_eval_dataset.py` | Manage the UC-backed v2 evaluation dataset (static records + tagged production traces with expectations) |
| `benchmarks/enable_monitoring.py` | Start, inspect, and stop server-side production monitoring scorers over UC-ingested traces |
| `benchmarks/align_judges.py` | Align Fleet judges with SME feedback via labeling sessions and MemAlign, then re-evaluate the baseline |

Legacy WebSocket and compatibility runtime scripts were retired with the
backend hard cutover. The evaluation entries above are the maintained
trusted-host CLI workflows.

Run commands from the repository root. `--help` is an inspection path, not
authorization to run a credentialed operation. Live scripts differ in admission:
some require `FLEET_LIVE=1`, while the maintained Daytona verifiers use
`runtime.live_enabled`. Follow the individual command's documented prerequisites.
Receipts prove only their recorded candidate, topology, workload, and outcome.

## Phase 1 Daytona stream canary

`live_phase1_stream_verify.py` is the narrow, credentialed closure proof for
live per-iteration Runtime Events on the default `daytona-recursive` policy. It
requires `runtime.live_enabled = true`, the selected `daytona-recursive` profile, the
provider environment names shown in the [profile matrix](../docs/reference/profile-matrix.md),
and a clean tracked candidate on a non-`main` branch. Existing environment
values win over `.env` values.

```bash
uv run python scripts/live_phase1_stream_verify.py \
  --output .scratch/fleet-rlm-recursive-runtime/evidence/daytona-dspy-stream-<run-id>.json
```

The receipt records only the candidate fingerprint, dependency versions,
selected non-secret model identifiers, bounded event timing/counts, and
boolean assertions. It never includes Attachment content, prompts, generated
code, provider responses, trace IDs, broker addresses, or credentials. This
canary is not a release proof: use `live_daytona_verify.py` for the broader
MVP/release scenario with durability and Sandbox-replacement coverage.

## Phase 2 Daytona recursive-child canary

`live_phase2_recursive_verify.py` is the credentialed Phase 2 proof for one
native DSPy recursive child on `[profiles.daytona-recursive]`. Run it only
after a committed Phase 1 receipt and retrospective, and only with explicit
live authorization. It requires `runtime.live_enabled = true`, the selected
recursive profile, the provider environment names shown in the [profile matrix](../docs/reference/profile-matrix.md),
and a clean
tracked candidate on a non-`main` branch. Existing environment values continue
to win over `.env` values.

```bash
uv run python scripts/live_phase2_recursive_verify.py \
  --output .scratch/fleet-rlm-recursive-runtime/evidence/daytona-dspy-recursive-<run-id>.json
```

The one-Turn scenario proves one dedicated child Sandbox with normal network
policy, the same Volume ID only at a private sibling scope, absent Root Python
state in the child, Root continuity, typed child and Root submissions, and
strict child cleanup. The receipt records only candidate identity, locked
versions, non-secret policy identifiers, bounded durations, and booleans. It
never contains prompts, answers, code, credentials, URLs, trace IDs, Sandbox
or Volume IDs, or broker details. The committed policy is the single
recursive `daytona-recursive` profile.

The live RLM latency gate is opt-in and never edits Fleet policy. Restart Fleet
with each candidate configuration, then label that active policy explicitly:

```bash
FLEET_LIVE=1 uv run python scripts/benchmarks/run_rlm_latency.py benchmark \
  --variant baseline --output .scratch/benchmark-reports/rlm-latency-baseline.json
```

`prepare-evaluation` and `evaluate` default to the probe-verified
`databricks:/databricks-qwen35-122b-a10b` judge endpoint. Override it with
`--judge-model` only when intentionally evaluating with a different
MLflow-supported endpoint (e.g. `gateway:/databricks-inkling` via a local
MLflow AI Gateway server); the Fleet DSPy model aliases are not automatically
valid MLflow judge endpoints.

The evaluation command uses the MLflow 3.16 GenAI API. The normal path keeps
the registered `correctness` and `evidence_coverage` judges. Use
`--judge-ab --evaluation-experiment-id <separate-id>` for the opt-in
rationale-first comparison; it evaluates both scorer variants in memory,
writes a bounded comparison receipt, and never mutates the canonical registry.
Promotion is a separate reviewed call to `ensure_registered` with
`generate_rationale_first=True`.

## Phase 3 MLflow receipt attachment

After the opt-in native feasibility lane writes its bounded JSON receipt, an
operator can attach that exact capability result to an existing campaign run:

```bash
FLEET_LIVE=1 uv run python scripts/benchmarks/attach_phase3_receipt.py \
  --run-id <campaign-run-id> \
  --receipt .scratch/fleet-rlm-recursive-runtime/evidence/daytona-phase3-native-live.json \
  --output .scratch/benchmark-reports/phase3-mlflow-attachment.json
```

The command validates the receipt schema and safety fields, uploads one
canonical `daytona-native-feasibility.json` artifact, and writes only bounded
`fleet.phase3.*` tags/metrics. It never starts a Turn or changes runtime or
capacity policy; a missing or failed MLflow operation is recorded as a failed
attachment receipt rather than presented as native success.

## Evaluation loop

The Databricks-backed quality loop composes three opt-in steps that all require
`FLEET_LIVE=1` and Databricks auth from the environment:

1. `benchmarks/rlm_eval_dataset.py ingest-static|ingest-traces` builds the v2
   UC dataset (`fleet-rlm-quality-v2`) with explicit expectations.
2. `benchmarks/enable_monitoring.py start` scores a sampled fraction of
   production `fleet_turn` traces server-side; `status`/`stop` manage the
   registration without touching Turn execution.
3. `benchmarks/align_judges.py prepare-labeling|align|reeval-baseline` opens an
   SME labeling session, distills judge guidelines with MemAlign, and re-runs
   the aligned baseline under a named run.
See `docs/how-to-guides/evaluation-optimization.md` for the full workflow.

## Runtime v2 adapter protocol comparison

Run the credential-free DSPy 3.3.1 protocol replay from the repository root:

```bash
uv run python -m scripts.benchmarks.runtime_v2 compare-adapters \
  --repetitions 5 --output .scratch/runtime-v2-adapter-comparison.json
```

The command executes stock JSONAdapter and Fleet adapters with zero, one, and
two parse repairs over versioned response fixtures, in both sync and async modes.
It records outcomes, physical provider admissions, latency distributions, source
and fixture digests, and fail-closed contract gates. The scripted Turn lane also
runs the deterministic `semantic-keywords/v1` content-presence scorer; this is not
an LLM quality judgment, and `live_semantic_gate` remains `not_exercised`. Output
creation is exclusive; choose a new filename for each receipt. The comparison does
not call a provider, Daytona, Postgres, or an MLflow server. Its scores measure
deterministic lifecycle/protocol behavior, not live semantic quality or production
latency/cost.

### PostgreSQL contention and query-plan receipts

`benchmarks/certify_postgres.py` runs the existing six-scenario contention lane
and writes a bounded, content-free receipt without printing driver errors. It
requires exported `FLEET_LIVE=1`, `FLEET_DATABASE_URL`, and an explicitly designated
exclusive test database (`FLEET_TEST_DATABASE_EXCLUSIVE=1`). It never loads dotenv,
applies migrations, or overwrites a receipt.

```bash
uv run python scripts/benchmarks/certify_postgres.py \
  --receipt .fleet-evidence/receipts/adr006/postgres-contention-new.json \
  --query-plans --query-plan-samples 64 --timeout 600
```

### Daytona SDK compatibility receipt

`benchmarks/certify_daytona_sdk.py` runs the bounded unit compatibility suite and
writes a content-free receipt once. It never loads dotenv or contacts Daytona.
Volume, sandbox, broker, upload, lifecycle, and public-event live surfaces are
recorded individually as `not_exercised`; they require a separately authorized
operator campaign and cannot be inferred from the unit receipt.

```bash
uv run python scripts/benchmarks/certify_daytona_sdk.py \
  --receipt .fleet-evidence/receipts/adr006/daytona-sdk-compatibility-unit.json
```

### SemanticChild warm-pool operator path

`daytona_warm_pool.py` is the only reconciliation path for the clean,
Volume-less SemanticChild image. The committed policy keeps it disabled and at
zero capacity. `plan` needs no provider contact; `check` reads the configured
pool; `reconcile` changes capacity only when `runtime.live_enabled` and the
explicit warm-pool policy permit it. It refuses ambiguous matching pools and
never runs from a Fleet Turn.

```bash
uv run python scripts/daytona_warm_pool.py plan
uv run python scripts/daytona_warm_pool.py check
uv run python scripts/daytona_warm_pool.py reconcile \
  --campaign semantic-child-rollout --spend-cap 10 --elapsed-seconds 1800 \
  --admission-limit 1 --sandbox-concurrency 1
```

Ownership is recorded durably in `fleet_warm_pool_ownership`; reconciliation
never accepts a caller-supplied pool id. Use `--adopt` on an explicit reconcile
only when taking ownership of an existing, uniquely matching provider pool.

### Deployed database-head inventory

`inventory_db_heads.py` is the separate P1A.01 operator tool for deployed or
continuation targets. It performs read-only connectivity, `alembic_version`, and
server-version checks, compares the observed heads with the repository graph,
and writes a content-free receipt exactly once. It never migrates, records the
database URL, or substitutes for the exclusive contention campaign.

```bash
FLEET_DATABASE_URL="$DEPLOYED_DATABASE_URL" \
  uv run python scripts/inventory_db_heads.py \
  --target production-primary \
  --receipt .fleet-evidence/receipts/adr006/db-head-production-primary.json
```

Run once per supported deployed target and any deployment of the earlier
migration branch before deciding whether an additive or merge migration is
needed. Do not run the contention command above against a deployed target.

The optional query-plan lane captures the actual Session-list, history, replay,
recovery and outbox SELECTs through repository calls. Receipts retain statement
digests, planner topology, costs and fixture scale; SQL parameters, predicates,
private names and exception text are excluded. The fixture is synthetic and does
not establish representative deployment performance. Skipped, incomplete or
failed campaigns cannot produce a passing certification result.
