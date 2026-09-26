# Maintained scripts

Run repository commands from the root with `uv run python`. The inventory below
distinguishes executable commands from implementation modules and data. Current
runtime policy comes from `config/fleet.toml`; a receipt describes only the
candidate and behavior its contract records.

## Repository checks and generated contracts

| Path | Role |
| --- | --- |
| `scripts/check_repo_hygiene.py` | Single entrypoint for AGENTS freshness, active docs, harness rules, script inventory, and active script references. `--editorial` adds local editorial checks. |
| `scripts/check_agents_md_freshness.py` | Support module for AGENTS checks; not a separate command. |
| `scripts/check_docs_quality.py` | Support module for active documentation checks; not a separate command. |
| `scripts/check_harness_engineering.py` | Support module for harness checks; not a separate command. |
| `scripts/check_codebase_tree.py` | Enforce code ownership, API route, and test-layout constraints. |
| `scripts/check_dependency_boundaries.py` | Enforce domain dependency direction and content boundaries. |
| `scripts/import_walk.py` | Shared AST import walker for architecture checkers. |
| `scripts/openapi_tools.py` | Generate or check the OpenAPI contract. |
| `scripts/generate_stream_fixture.py` | Generate or check the deterministic TUI stream fixture. |
| `scripts/generate_tui_chunk_validation.py` | Generate or check TUI chunk-validation tables. |
| `scripts/generate_profile_matrix.py` | Generate or check the TOML-derived profile matrix. |
| `scripts/validate_release.py` | Validate package metadata, artifacts, and release hygiene. |
| `scripts/release_smoke.py` | Exercise the installed wheel through a local smoke check. |
| `scripts/normalize_release_artifacts.py` | Normalize wheel and sdist metadata for reproducible release identities. |

Use the matching Make target where one exists. Generated contracts and the
profile matrix must be regenerated from their owning source, never edited by
hand. Codebase-tree and dependency-boundary checks remain separate because they
guard different architectural rules.

## Database, Daytona, and release operations

| Path | Role |
| --- | --- |
| `scripts/db_init.py` | Apply Alembic migrations to the configured database target. |
| `scripts/migrate_sqlite_to_postgres.py` | Run the explicit one-time database import. |
| `scripts/lakebase_preflight.py` | Run the sanitized Lakebase readiness preflight. |
| `scripts/daytona_snapshot.py` | Plan, create, check, or verify immutable Daytona snapshots. |
| `scripts/benchmark_daytona_lifecycle.py` | Measure bounded Daytona lifecycle behavior. |
| `scripts/live_daytona_verify.py` | Run the native semantic FastAPI contract and attachment/artifact durability contract on one committed candidate. |
| `scripts/live_recursive_batch_canary.py` | Run the separately authorized recursive-batch canary; see the scope limits below. |
| `scripts/validate_mlflow_tracing.py` | Run the selected-policy MLflow tracing smoke check. |
| `scripts/deployment_observability.py` | Inspect release observability inputs. |
| `scripts/circleci_trigger_release.py` | Trigger and await the release workflow. |

Credentialed commands require explicit operator intent and their documented
policy, target, credential, spend, and receipt arguments. The live Daytona
verifier requires `FLEET_LIVE=1`, the `daytona-native` profile, explicit bounded
Root and Sub model IDs, provider credentials, a clean tracked candidate branch,
and a new ignored or out-of-repository receipt path. `--help` does not load
credentials or authorize a run.

The native verifier records that the native single and ordered batch semantic
calls pass through FastAPI, that staged attachment bytes are readable during a
Run, that Artifact bytes remain readable after sandbox replacement with the
shared volume, and that the tested cleanup paths settle. It does not exercise
recursive child execution or establish provider containment, child promotion,
release readiness, or deployment.

The recursive-batch canary in `scripts/live_recursive_batch_canary.py` requires
`FLEET_LIVE=1`, enabled live policy, a clean non-main candidate, and a new
receipt outside the repository. Its receipt checks two ordered child outcomes,
observed peak concurrency of two, root reuse on a second turn, trace hierarchy,
cleanup, and restored admission. It is one canary run; it does not establish
containment certification, comparative quality, promotion, release, or
deployment. Keep Phase 6 status and evidence in its active ledger.

## Current evaluation and operator tools

| Path | Role |
| --- | --- |
| `scripts/benchmarks/run_rlm_latency.py` | Run the current Phase 6 latency, evaluation, and campaign workflows. |
| `scripts/benchmarks/run_routing_eval.py` | Run bounded routing plans and live evaluation. |
| `scripts/benchmarks/run_oolong_predict.py` | Run the official Oolong prediction adapter. |
| `scripts/benchmarks/rlm_eval_dataset.py` | Manage the evaluation dataset and trace-linked examples. |
| `scripts/benchmarks/certify_mlflow.py` | Run bounded MLflow backend certification. |
| `scripts/benchmarks/certify_postgres.py` | Run bounded PostgreSQL and migration certification. |
| `scripts/benchmarks/enable_monitoring.py` | Manage operator-gated production monitoring. |
| `scripts/benchmarks/align_judges.py` | Prepare and run operator-gated judge alignment. |
| `scripts/benchmarks/annotate_traces.py` | Annotate selected evaluation traces. |
| `scripts/benchmarks/manage_prompts.py` | Manage prompt registry entries and trace links. |

## Support modules and data

These files are used by retained commands and are not standalone operator
workflows.

| Path | Role |
| --- | --- |
| `scripts/benchmarks/__init__.py` | Python package marker. |
| `scripts/benchmarks/campaign.py` | Shared bounded evaluation campaign support. |
| `scripts/benchmarks/corpus_chain.py` | Deterministic corpus and chain support. |
| `scripts/benchmarks/judges.py` | Shared evaluation judge support. |
| `scripts/benchmarks/scorers.py` | Evaluation scorers used by retained runners. |
| `scripts/benchmarks/usage_cost.py` | Usage and cost accounting support. |
| `scripts/benchmarks/oolong/__init__.py` | Oolong helper package marker. |
| `scripts/benchmarks/oolong/adapter.py` | Oolong prediction adapter implementation. |
| `scripts/benchmarks/oolong/scoring.py` | Oolong scoring implementation. |
| `scripts/benchmarks/oolong/fixture_validation_row.json` | Fixed row used to validate the Oolong fixture contract. |
| `scripts/benchmarks/phase6_evaluation_cases.json` | Frozen Phase 6 evaluation case manifest. |

Historical ledgers and receipts remain evidence records and are not rewritten
by this inventory. Current behavior is defined by code, tests, `config/fleet.toml`,
and generated-contract checks.
