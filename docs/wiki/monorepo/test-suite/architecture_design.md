Tests are organized into four parallel Python packages under `tests/`, each a package with its own `__init__.py`:
- `unit/backend/`: fast, isolated unit tests mirroring the source tree (`chat/`, `daytona/`, `files/`, `rlm/`, `sessions/`) plus top-level module tests.
- `contracts/backend/`: HTTP/API contract tests using FastAPI's `TestClient` against `create_testing_app` from `fleet_rlm.composition.testing`, asserting response shapes, headers, and error codes.
- `live/backend/`: slow integration tests that spin up real services; shared helpers like `_database.py` run Alembic migrations against fresh databases via `upgrade_to_head`.
- `e2e/`: process-level smoke tests that invoke installed CLI binaries through `subprocess`.

A single root `conftest.py` centralizes pytest hooks: it auto-applies suite markers (`unit`, `integration`, `contracts`, `e2e`) based on file path, marks DB-dependent integration tests with `db`, sanitizes parametrize IDs for CI, and post-processes JUnit XML output to align classnames for Smarter Testing. All tests set `LITELLM_LOCAL_MODEL_COST_MAP=true` to avoid remote model-cost fetches.