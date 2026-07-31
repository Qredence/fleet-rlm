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
| `benchmarks/run_official_oolong.py` | Run the official Oolong scorer against a live Fleet API using Attachments |
| `benchmarks/run_native_long_context.py` | Measure native whole-value URL context at 1/5/10 MiB and emit the paging decision receipt |
| `benchmarks/run_rlm_latency.py` | Compare live Fleet RLM configuration variants and run the MLflow-native five-task quality gate |
| `daytona_snapshot.py` | Explicitly create or check the immutable Fleet Daytona Snapshot |
| `codex_feedback_loop.py` | Run the local Codex feedback-loop probes |
| `deployment_observability.py` | Inspect deployment observability inputs |
| `validate_mlflow_tracing.py` | Emit and validate a local or Managed Databricks trace using the selected Fleet TOML policy |

Legacy WebSocket, optimization, evaluation, and compatibility
runtime scripts were retired with the backend hard cutover.

The live RLM latency gate is opt-in and never edits Fleet policy. Restart Fleet
with each candidate configuration, then label that active policy explicitly:

```bash
FLEET_LIVE=1 uv run python scripts/benchmarks/run_rlm_latency.py benchmark \
  --variant baseline --output .scratch/benchmark-reports/rlm-latency-baseline.json
```

`prepare-evaluation` and `evaluate` require `--judge-model` naming an endpoint
supported directly by MLflow. The Fleet AI Gateway aliases used by DSPy are not
automatically valid MLflow judge endpoints.
