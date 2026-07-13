# Documentation Home

`fleet-rlm` now has one Python backend: the RLM-native package at
`src/fleet_rlm/`. It exposes a compact FastAPI/SSE contract backed by DSPy,
Daytona, and SQLAlchemy/Alembic.

## Start Here

1. [Architecture](architecture.md)
2. [Backend API](reference/http-api.md)
3. [CLI](reference/cli.md)
4. [Testing strategy](how-to-guides/testing-strategy.md)
5. [Terminal UI](how-to-guides/terminal-tui.md)
6. [Agent harness](agent-harness/README.md)

## Reference

- [Documentation overview](README.md)
- [Complete table of contents](SUMMARY.md)
- [Reference index](reference/index.md)
- [Codebase map](reference/codebase-map.md)
- [Source layout](reference/source-layout.md)
- [Database](reference/database.md)
- [Runtime execution flow](explanation/agent-runtime-execution-flow.md)
- [Current implementation plans](plan-implementation/README.md)

## Source of Truth

- Python backend: `src/fleet_rlm/`
- HTTP contract: `openapi.yaml`
- schema: `migrations/`
- backend validation: `Makefile`, `tests/unit/backend/`,
  `tests/contracts/backend/`, and `tests/live/backend/`
