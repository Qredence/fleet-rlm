# Backend Testing Strategy

The cutover gate covers the backend and the maintained `tools/fleet-tui/`
client. Generated API types are synchronized only through `make api-sync`.

## Test Suites

| Suite | Path | Purpose |
| --- | --- | --- |
| Unit | `tests/unit/backend/` | domain, adapters, configuration, and route modules |
| Contract | `tests/contracts/backend/` | API, persistence, packaging, and boundary contracts |
| Live durability | `tests/live/backend/test_b5_attachment_artifact_durability.py` | Workspace Volume Attachment/Artifact persistence |
| Live workspace | `tests/live/backend/test_phase7_workspace_durability.py` | Session Workspace persistence across Sandbox replacement |

## Local Gate

```bash
# from repo root
make check
```

The default Make test targets explicitly mask local live `FLEET_*` credentials
so a developer's `.env` cannot silently switch unit, contract, or end-to-end
tests into live composition. Database, Deno-runtime, and live-provider lanes
remain separate. Tests marked `deno` are excluded from the normal fast and
CircleCI split lanes.

This runs format, Ruff, type, unit/contract tests, backend OpenAPI drift, and
the codebase boundary check, plus the pinned TUI Biome/TypeScript/Vitest lane.
The focused commands are:

```bash
uv run pytest tests/unit/backend tests/contracts/backend -q
uv run ruff check src/fleet_rlm tests/unit/backend tests/contracts/backend
uv run ty check src/fleet_rlm
uv run python scripts/openapi_tools.py check
uv run python scripts/check_harness_engineering.py
git diff --check
pnpm --dir tools/fleet-tui install --frozen-lockfile
pnpm --dir tools/fleet-tui run format:check
pnpm --dir tools/fleet-tui run lint
pnpm --dir tools/fleet-tui run typecheck
pnpm --dir tools/fleet-tui run test
```

## Deno Gate

Deno-runtime contracts use the `deno` pytest marker and run only in the
dedicated lane:

```bash
# from repo root; requires Deno on PATH
make test-deno
```

CircleCI installs exactly Deno 2.9.2 under `$HOME/.deno`, exports
`$HOME/.deno/bin` on `PATH`, records `deno --version`, and runs
`make test-deno` as a required workflow job. The contract is deterministic and
forbids provider network calls; it validates DSPy's real default
Deno/Pyodide interpreter rather than live model quality.

## Database Gate

Alembic owns production schema creation. Against an empty configured database:

```bash
uv run alembic upgrade head
uv run alembic check
```

Tests may use explicit helpers to create ephemeral SQLite schemas.

## Live Gate

Live tests require canonical `FLEET_*` credentials and explicit opt-in.
The Daytona MVP proof loads repo `.env` via `python-dotenv` (`override=False`)
and defaults models to bare `deepseek-v4-flash-free` when unset
(`normalize_model_id` adds the `openai/` provider prefix for `dspy.LM`):

```bash
FLEET_LIVE=1 uv run pytest tests/live/backend/test_fleet_rlm_daytona_mvp.py -q -n 0 --timeout=900
FLEET_LIVE=1 uv run pytest tests/live/backend/test_b5_attachment_artifact_durability.py -q
FLEET_LIVE=1 uv run pytest tests/live/backend/test_phase7_workspace_durability.py -q
```

No pre-cutover or provider-specific environment aliases are supported.

## Packaging and Smoke

```bash
uv build
uv run python scripts/validate_release.py wheel
uv run fleet --help
uv run fleet-rlm --help
uv run python -c 'from fleet_rlm.main import app; print(app.title)'
```
