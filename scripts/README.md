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
| `live_daytona_tunnel_probe.py` | Run the development-only strict Daytona egress smoke through two Cloudflare HTTPS origins |
| `benchmark_daytona_lifecycle.py` | Benchmark full Daytona create-through-first-execution lifecycle and select retained versus per-Turn mode |
| `benchmarks/run_official_oolong.py` | Run the official Oolong scorer against a live Fleet API using Attachments |
| `benchmarks/run_native_long_context.py` | Measure native whole-value URL context at 1/5/10 MiB and emit the paging decision receipt |
| `benchmarks/run_rlm_latency.py` | Compare live Fleet RLM configuration variants and run the MLflow-native five-task quality gate |
| `daytona_snapshot.py` | Explicitly create or check the immutable Fleet Daytona Snapshot |
| `codex_feedback_loop.py` | Run the local Codex feedback-loop probes |
| `deployment_observability.py` | Inspect deployment observability inputs |
| `validate_mlflow_tracing.py` | Emit and validate a local or Managed Databricks trace using the selected Fleet TOML policy |
| `benchmarks/rlm_eval_dataset.py` | Manage the UC-backed v2 evaluation dataset (static records + tagged production traces with expectations) |
| `benchmarks/enable_monitoring.py` | Start, inspect, and stop server-side production monitoring scorers over UC-ingested traces |
| `benchmarks/align_judges.py` | Align Fleet judges with SME feedback via labeling sessions and MemAlign, then re-evaluate the baseline |
| `optimize/optimize_signature_omni.py` | Optimize the Fleet RLM signature with a GEPA omni-style explore/continue composition |
| `optimize/optimize_signature_gepa.py` | Run the fail-closed preflight or development-only synthetic GEPA smoke |

Legacy WebSocket and compatibility runtime scripts were retired with the
backend hard cutover. The evaluation and optimization entries above are the
maintained trusted-host CLI workflows.

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

## Evaluation and optimization loop

The Databricks-backed quality loop composes four opt-in steps that all require
`FLEET_LIVE=1` and Databricks auth from the environment:

1. `benchmarks/rlm_eval_dataset.py ingest-static|ingest-traces` builds the v2
   UC dataset (`fleet-rlm-quality-v2`) with explicit expectations.
2. `benchmarks/enable_monitoring.py start` scores a sampled fraction of
   production `fleet_turn` traces server-side; `status`/`stop` manage the
   registration without touching Turn execution.
3. `benchmarks/align_judges.py prepare-labeling|align|reeval-baseline` opens an
   SME labeling session, distills judge guidelines with MemAlign, and re-runs
   the aligned baseline under a named run.
4. `optimize/optimize_signature_omni.py` explores and continues signature
   candidates against the dataset with GEPA; the best candidate is written for
   human review and never auto-applied.

See `docs/how-to-guides/evaluation-optimization.md` for the full workflow and
`scripts/optimize/AGENTS.md` for optimizer-lane constraints.
