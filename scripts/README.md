# Script Inventory

`scripts/` is a supported helper surface, not a catch-all archive.

Support boundary:

- Keep common developer flows on `make`, `fleet`, and `fleet-rlm`.
- Retained Python helpers must support `uv run python scripts/<name>.py --help` without performing work.
- If a helper is not wired into current code, tests, CI, docs, or this inventory, it should be removed instead of kept as historical debris.

## Core Workflow

| Script | Purpose | Required env | Canonical invocation |
| --- | --- | --- | --- |
| `build_ui.py` | Repo wrapper around `src/fleet_rlm/ui/build.py`, which builds `src/frontend` and syncs packaged UI assets into `src/fleet_rlm/ui/dist` | `pnpm` on `PATH` | `uv run python scripts/build_ui.py` |
| `check_agents_md_freshness.py` | Validate `AGENTS.md` files against current repo paths, commands, and links | None | `uv run python scripts/check_agents_md_freshness.py` |
| `check_docs_quality.py` | Validate docs links, reachability, and contract sanity | None | `uv run python scripts/check_docs_quality.py` |
| `check_harness_engineering.py` | Validate root agent-map budget, harness docs, `.codex` config, script inventory, and structural boundaries | None | `uv run python scripts/check_harness_engineering.py` |
| `codex_feedback_loop.py` | Run the safe local Codex feedback loop and write a concise report | None for `--profile safe`; running app for `--profile app` | `uv run python scripts/codex_feedback_loop.py --profile safe` |
| `openapi_tools.py` | Generate or validate the root OpenAPI contract | Backend dependencies | `uv run python scripts/openapi_tools.py generate` |
| `validate_release.py` | Run release hygiene, metadata, and wheel integrity checks | Build artifacts for `wheel` mode | `uv run python scripts/validate_release.py metadata` |
| `run_duplicate_check.zsh` | Run `jscpd` against handwritten source blocks | `src/frontend/node_modules` installed | `./scripts/run_duplicate_check.zsh` |

## Evaluation

| Script | Purpose | Required env | Canonical invocation |
| --- | --- | --- | --- |
| `evaluate_rlm_capabilities.py` | Run S-NIAH, OOLONG, and workspace benchmark harnesses | `DSPY_LM_MODEL`, `DSPY_LLM_API_KEY` or `DSPY_LM_API_KEY`, Daytona creds | `uv run python scripts/evaluate_rlm_capabilities.py --benchmark all` |
| `oolong_official_eval.py` | Run the official Prime Intellect OOLONG adapter | Same as above plus HuggingFace access as needed | `uv run python scripts/oolong_official_eval.py --subset synth --split validation --limit 10` |
| `consolidate_rlm_results.py` | Build a single `RESULTS.md` from benchmark summaries | Generated benchmark summaries under `output/` | `uv run python scripts/consolidate_rlm_results.py --input-dir output/rlm-eval-full` |
| `benchmarks/sniah.py` | Generate and score S-NIAH benchmark data | None | `uv run python scripts/benchmarks/sniah.py --generate` |
| `benchmarks/oolong.py` | Generate and score synthetic OOLONG benchmark data | None | `uv run python scripts/benchmarks/oolong.py --generate` |

## Operator Scripts

| Script | Purpose | Required env | Canonical invocation |
| --- | --- | --- | --- |
| `db_init.py` | Validate Postgres connectivity and apply Alembic migrations | `DATABASE_ADMIN_URL` or `DATABASE_URL` | `uv run python scripts/db_init.py` |
| `db_smoke.py` | Exercise repository persistence against a disposable Postgres database | `DATABASE_URL` or `DATABASE_ADMIN_URL` | `uv run python scripts/db_smoke.py` |
| `dev_issue_token.py` | Issue an `AUTH_MODE=dev` JWT for local testing | `DEV_JWT_SECRET` or `--secret` | `uv run python scripts/dev_issue_token.py --tid tenant-123 --oid user-456 --email alice@example.com --name Alice` |
| `validate_env.py` | Validate `.claude/agents` or Daytona runtime prerequisites | Daytona/LM env for `daytona` mode | `uv run python scripts/validate_env.py daytona --repo https://github.com/qredence/fleet-rlm.git --ref main` |
| `live_daytona_verify.py` | Verify live Daytona persistent-volume layout and session restore paths | `DAYTONA_API_KEY`, `DAYTONA_API_URL`, optional `DAYTONA_TARGET` | `uv run python scripts/live_daytona_verify.py` |
| `live_concurrency_verify.py` | Verify live Daytona sandbox slot limits, busy behavior, and cleanup release | Same as above plus `FLEET_MAX_CONCURRENT_SANDBOXES=2` for a bounded test | `FLEET_MAX_CONCURRENT_SANDBOXES=2 uv run python scripts/live_concurrency_verify.py` |
| `validate_rlm_e2e_trace.py` | Run the live websocket tracing validation harness | Running API server plus DB/auth/runtime env | `uv run python scripts/validate_rlm_e2e_trace.py --server-url http://127.0.0.1:8000` |
| `deployment_observability.py` | Emit release summaries and optional PostHog deployment markers | GitHub Actions env and optional PostHog creds | `uv run python scripts/deployment_observability.py --environment production --package-name fleet-rlm` |
| `ensure_frontend_entrypoint.py` | Create `src/frontend/dist/client/index.html` from TanStack Start build metadata when prerendering is disabled for CI/release builds | Existing `src/frontend/dist` build | `uv run python scripts/ensure_frontend_entrypoint.py` |
| `mlflow_cli.py` | Export/evaluate MLflow-backed datasets, optimize programs, and list/delete persisted GenAI scorers | `MLFLOW_TRACKING_URI` and any required MLflow auth | `uv run python scripts/mlflow_cli.py export --output artifacts/mlflow/annotated-traces.json` |
