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
| `codex_feedback_loop.py` | Run the local Codex feedback-loop probes |
| `deployment_observability.py` | Inspect deployment observability inputs |
| `dev_issue_token.py` | Generate a local development issue token |

Legacy WebSocket, optimization, evaluation, snapshot, MLflow, and compatibility
runtime scripts were retired with the backend hard cutover.
