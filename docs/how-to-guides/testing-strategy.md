# Backend Testing Strategy

The cutover gate is backend-only. It does not run frontend tests or synchronize
frontend-generated API contracts.

## Test Suites

| Suite | Path | Purpose |
| --- | --- | --- |
| Unit | `tests/unit/backend/` | domain, adapters, configuration, and route modules |
| Contract | `tests/contracts/backend/` | API, persistence, packaging, and boundary contracts |
| Live L1 | `tests/live/backend/test_exit_bar_l1_promotion.py` | one-workspace Daytona/DSPy lifecycle |
| Live L2 | `tests/live/backend/test_exit_bar_l2_adversarial.py` | cross-workspace isolation and cancellation |

## Local Gate

```bash
# from repo root
make check
```

The default Make test targets explicitly mask local live `FLEET_*` credentials
so a developer's `.env` cannot silently switch unit, contract, or end-to-end
tests into live composition. Database and live-provider lanes remain separate.

This runs format, Ruff, type, unit/contract tests, backend OpenAPI drift, and
the codebase boundary check. The focused commands are:

```bash
uv run pytest tests/unit/backend tests/contracts/backend -q
uv run ruff check src/fleet_rlm tests/unit/backend tests/contracts/backend
uv run ty check src/fleet_rlm
uv run python scripts/openapi_tools.py check
uv run python scripts/check_harness_engineering.py
git diff --check
```

## Database Gate

Alembic owns production schema creation. Against an empty configured database:

```bash
uv run alembic upgrade head
uv run alembic check
```

Tests may use explicit helpers to create ephemeral SQLite schemas.

## Live Gate

Live tests require canonical `FLEET_*` credentials and explicit opt-in:

```bash
FLEET_LIVE=1 uv run pytest tests/live/backend/test_exit_bar_l1_promotion.py -q
FLEET_LIVE=1 uv run pytest tests/live/backend/test_exit_bar_l2_adversarial.py -q
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
