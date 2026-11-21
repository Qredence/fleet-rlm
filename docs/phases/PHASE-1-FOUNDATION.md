# Phase 1 — Foundation, Python 3.13, Daytona Snapshot & Neon Postgres 18

## Objective
Establish the clean Python 3.13 project scaffold, pinned Daytona snapshot, flat configuration, Neon Postgres 18 schema (4 tables with `uuidv7()` and root-only partial unique index locking), Alembic migration pipeline, and CI architecture guardrails (`import-linter`).

## Files to Create
- `backend/pyproject.toml`
- `backend/.env.example`
- `backend/snapshot/Dockerfile`
- `backend/src/fleet_rlm/__init__.py`
- `backend/src/fleet_rlm/config.py`
- `backend/src/fleet_rlm/db.py`
- `backend/alembic.ini`
- `backend/alembic/env.py`
- `backend/alembic/versions/0001_initial_schema.py`
- `backend/tests/test_phase1_locking.py`
- `docker-compose.yml` (external MLflow 3.18.0 for local dev)

## Technical Specifications

### 1. Packaging & CI (`backend/pyproject.toml`)
- `requires-python = ">=3.13,<3.14"`.
- Runtime dependencies: `fastapi[standard]>=0.165.0`, `dspy==3.5.0`, `daytona==0.232.0`, `sqlalchemy[asyncio]>=2.1.9`, `alembic>=1.20.1`, `psycopg[binary]>=3.3.9`, `pydantic-settings>=2.16.3`, `pyyaml>=6.0.2`, `mlflow>=3.18.0`.
- Dev dependencies: `pytest>=8.3.0`, `pytest-asyncio>=0.25.0`, `httpx>=0.28.0`, `ruff>=0.9.0`, `import-linter>=2.1`.
- Configure `[tool.importlinter]` with two contracts:
  1. `layers`: `fleet_rlm.api` -> `fleet_rlm.agent | fleet_rlm.skills | fleet_rlm.interpreter | fleet_rlm.db` -> `fleet_rlm.config`.
  2. `independence`: `fleet_rlm.db`, `fleet_rlm.interpreter`, `fleet_rlm.skills`.

### 2. Daytona Snapshot (`backend/snapshot/Dockerfile`)
- Base image: `python:3.13-slim`.
- Install `uv`, then install system packages: `dspy==3.5.0`, `numpy>=2.2.0`, `pandas>=2.2.3`, `pyarrow>=19.0.0`, `openpyxl>=3.1.5`, `pillow>=11.1.0`, `matplotlib>=3.10.0`, `pypdf>=5.3.0`, `scipy>=1.15.0`.
- Copy read-only `skills/` into `/opt/skills/`.
- Create `/workspace/files` and `/workspace/artifacts`; set `WORKDIR /workspace`.

### 3. Configuration (`backend/src/fleet_rlm/config.py`)
- Single `Settings(BaseSettings)` with `env_prefix="FLEET_"`, `env_file=".env"`, `extra="ignore"`.
- Fields:
  - Neon: `database_url`, `database_direct_url`, `db_pool_size=2`, `db_max_overflow=5`, `db_pool_recycle_seconds=300`.
  - API: `api_bearer_token: SecretStr | None = None`, `cors_origins: list[str] = ["http://localhost:5173"]`, `max_concurrent_turns_per_worker=16`, `stale_run_timeout_seconds=900`.
  - Daytona: `daytona_api_key: SecretStr`, `daytona_target: str | None = None`, `daytona_volume_id: str`, `daytona_snapshot="fleet-rlm-py313-dspy35-v1"`, `daytona_auto_stop_minutes=15`, `daytona_exec_timeout_seconds=120`.
  - DSPy & Jev: `llm_model`, `sub_llm_model`, `llm_base_url: str | None = None`, `llm_api_key: SecretStr`, `decision_model="typesafe:jev-latest"`.
  - Guardrails: `rlm_max_iters=20`, `rlm_max_llm_calls=50`, `rlm_max_output_chars=10_000`, `rlm_max_depth=4`, `history_max_turns=8`, `history_max_chars=16_000`.
  - Skills & Artifacts: `skills_dir=Path("skills")`, `optimized_program_path: Path | None = None`, `skill_selector_path: Path | None = None`, `memory_gate_path: Path | None = None`.
  - MLflow: `mlflow_tracking_uri: str | None = None`, `mlflow_experiment="fleet-rlm"`.

### 4. Neon Postgres 18 Schema (`backend/src/fleet_rlm/db.py` & `0001_initial_schema.py`)
Enable `CREATE EXTENSION IF NOT EXISTS vector;` and create exactly 4 tables using `server_default=text("uuidv7()")`:
1. **`sessions`**: `id`, `title`, `daytona_sandbox_id` (unique), `history_json` (`JSONB`, default `{"messages": []}`), `model_override`, `created_at`, `updated_at`.
2. **`runs`**: `id`, `session_id` (FK CASCADE), `parent_run_id` (self-FK CASCADE, nullable), `root_run_id`, `depth` (`SMALLINT`, `CHECK 0..8`), `kind` (`'root' | 'subagent' | 'llm_query'`), `prompt`, `answer`, `ui_parts` (`JSONB`, default `[]`), `status` (`'running' | 'completed' | 'failed' | 'cancelled'`), `error`, `created_at`, `finished_at`.
   - **Partial unique index:** `uq_runs_one_running_root_per_session` ON `runs (session_id)` WHERE `parent_run_id IS NULL AND status = 'running'`.
   - Supporting indexes: `idx_runs_root_tree (root_run_id, id)` and `idx_runs_session_roots (session_id, id DESC) WHERE parent_run_id IS NULL`.
3. **`files`**: `id`, `session_id` (FK CASCADE), `path`, `sha256` (`CHAR(64)`), `size_bytes` (`BIGINT`), `kind` (`'input' | 'artifact'`), `created_at`, `UNIQUE (session_id, path)`.
4. **`memories`**: `id`, `session_id` (nullable FK CASCADE; `NULL` = global), `content`, `approved` (`BOOLEAN`, default `false`), `search_tsv` (`TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED`), `created_at`. GIN index on `search_tsv`.

### 5. Repository & Pool Rules (`db.py` & `alembic/env.py`)
- `create_db(settings)` uses `postgresql+psycopg://` with `connect_args={"prepare_threshold": None}`, `pool_pre_ping=True`, `pool_recycle=settings.db_pool_recycle_seconds`.
- Implement: `create_session`, `get_session_or_raise`, `acquire_root_run` (raises `TurnConflictError` on `IntegrityError`), `create_child_run`, `finish_run` (single transaction updating `runs` + `sessions.history_json`), `reap_stale_runs`, `get_session_ui_messages`, `upsert_file_record`, `list_session_files`, `search_memories`, `save_or_update_memory`.
- `alembic/env.py` must read `Settings().database_direct_url` with `NullPool`.

## Exit Gate (Must Pass Before Phase 2)
```bash
cd backend
uv sync --python 3.13
uv run alembic upgrade head
uv run ruff check .
uv run lint-imports
uv run pytest tests/test_phase1_locking.py
```
- **Acceptance criteria:** `test_phase1_locking.py` runs against Neon pooled URL and proves: (a) two concurrent `acquire_root_run` calls yield 1 winner and 1 `TurnConflictError`; (b) parallel `create_child_run` calls succeed while root is running; (c) `finish_run` releases the lock for the next root turn.
