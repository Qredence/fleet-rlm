# Maintained scripts

Run commands from the repository root with `uv run python`. `--help` is safe
inspection, not authorization for a credentialed operation. Current runtime
policy comes from `config/fleet.toml`: the selectable path is broker-backed
`legacy`, and Fleet child RLM tools are disabled unless an explicit profile
enables them. A receipt proves only its recorded candidate and topology.

## Deterministic generation and repository checks

| Script | Purpose |
| --- | --- |
| `openapi_tools.py` | Generate or check backend OpenAPI. |
| `generate_stream_fixture.py` | Generate or check the deterministic TUI stream fixture. |
| `generate_tui_chunk_validation.py` | Generate or check TUI chunk-validation tables. |
| `generate_profile_matrix.py` | Generate or check the TOML-derived profile matrix. |
| `check_codebase_tree.py` | Enforce canonical import and route boundaries. |
| `check_dependency_boundaries.py` | Check domain dependency directions. |
| `check_docs_quality.py` | Check active documentation links and structure. |
| `check_agents_md_freshness.py` | Check agent-guide reachability. |
| `check_harness_engineering.py` | Check repository harness conventions. |
| `validate_release.py` / `release_smoke.py` | Validate package metadata, artifacts, and installed-wheel smoke behavior. |

Use the matching Make target where one exists. Generated contracts and the
profile matrix must be regenerated from their owning source, never edited by
hand.

## Local maintenance and operator entrypoints

| Script | Purpose |
| --- | --- |
| `db_init.py` | Apply Alembic to the configured database target. |
| `migrate_sqlite_to_postgres.py` | Run the explicit one-time database import. |
| `daytona_snapshot.py` | Plan, create, check, or verify immutable Daytona snapshots. |
| `daytona_warm_pool.py` | Plan, inspect, or explicitly reconcile the disabled-by-default SemanticChild pool. |
| `inventory_db_heads.py` | Record a read-only deployed database-head inventory. |
| `lakebase_preflight.py` | Run the sanitized Lakebase readiness preflight. |
| `validate_mlflow_tracing.py` | Validate tracing for the selected policy. |
| `codex_feedback_loop.py` | Run local Codex feedback-loop probes. |
| `deployment_observability.py` | Inspect release observability inputs. |
| `circleci_trigger_release.py` | Trigger and await the release workflow. |

These commands may contact providers or mutate external state. Invoke them only
with the required explicit operator authorization and their documented policy,
credential, target, spend, and receipt arguments. They do not promote a
snapshot, enable paid capacity, or certify a deployment by themselves.

## Live verification and evaluation entrypoints

| Script | Purpose |
| --- | --- |
| `live_phase1_stream_verify.py` | Run the narrow live stream canary. |
| `live_phase2_recursive_verify.py` | Run the opt-in recursive-child canary. |
| `live_daytona_verify.py` | Run the broader Daytona MVP and durability verifier. |
| `live_p27_snapshot_verify.py` | Seal a reduced-snapshot probe receipt. |
| `benchmark_daytona_lifecycle.py` | Measure Daytona lifecycle behavior. |
| `benchmarks/run_phase4_campaign.py` | Run the sealed four-arm recursion ablation. |
| `benchmarks/run_rlm_latency.py` / `run_routing_eval.py` | Run bounded latency, quality, or routing evaluations. |
| `benchmarks/certify_mlflow.py` / `certify_postgres.py` / `certify_daytona_sdk.py` | Run bounded certification lanes. |
| `benchmarks/record_mlflow_campaign.py` / `attach_phase3_receipt.py` | Attach bounded evidence to MLflow. |
| `benchmarks/rlm_eval_dataset.py`, `enable_monitoring.py`, `align_judges.py` | Manage the operator-gated evaluation loop. |
| `benchmarks/runtime_v2.py` / `corpus_chain.py` | Run deterministic protocol and corpus fixtures. |

`phase4_campaign.py`, `phase4_api_client.py`, `phase4_api_server.py`,
`phase4_api_fake_server.py`, `adapter_replay.py`, and `campaign.py` are support
modules for these entrypoints, not standalone operator workflows. Keep dated
campaign results in the ADR 006 status ledger; do not convert partial, local, or
failed receipts into a product or certification claim.

## Required boundaries

- Prefer `make check-docs`, `make api-check`, `make stream-check`, and
  `make profile-matrix` for deterministic verification.
- Live Daytona and provider commands require the selected policy to allow live
  execution and require explicit operator intent; never infer or load secrets
  merely to satisfy a check.
- Use a new receipt path for each operator run. Do not overwrite receipts,
  promote configured snapshot references, or alter profiles as a side effect of
  a verification command.
- Historical P-phase receipts remain evidence records. Current behavior comes
  from code, tests, `config/fleet.toml`, and generated contract checks.
