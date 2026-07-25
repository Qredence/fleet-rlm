# Maintained scripts

| Script | Purpose |
| --- | --- |
| `db_init.py` | Upgrade a fresh `FLEET_DATABASE_URL` database to Alembic head |
| `openapi_tools.py` | Generate or check backend-only `openapi.yaml` |
| `check_codebase_tree.py` | Enforce canonical import and route boundaries |
| `check_harness_engineering.py` | Validate repository agent-harness contracts |
| `check_docs_quality.py` | Validate documentation structure and links |
| `check_agents_md_freshness.py` | Validate agent-guide reachability |
| `validate_release.py` | Validate package metadata and wheel contents |
| `live_daytona_verify.py` | Run the opt-in Daytona MVP proof and validate its bounded JSON receipt |
| `benchmark_daytona_lifecycle.py` | Benchmark full Daytona create-through-first-execution lifecycle and select retained versus per-Turn mode |
| `benchmarks/evaluate_oolong.py` | Run the bounded OOLONG benchmark against a live Fleet API and emit a scored receipt |
| `daytona_snapshot.py` | Explicitly create or check the immutable Fleet Daytona Snapshot |
| `codex_feedback_loop.py` | Run the local Codex feedback-loop probes |
| `deployment_observability.py` | Inspect deployment observability inputs |
| `validate_mlflow_tracing.py` | Emit and validate a Databricks Unity Catalog MLflow smoke trace |

Legacy WebSocket, optimization, evaluation, and compatibility
runtime scripts were retired with the backend hard cutover.
