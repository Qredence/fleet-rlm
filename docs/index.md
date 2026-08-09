# Documentation Home

Fleet RLM has one Python backend under `src/fleet_rlm/` and one maintained
development client under `tools/fleet-tui/`. It exposes a compact Session-first
FastAPI/SSE contract backed by DSPy, Daytona, and SQLAlchemy/Alembic.

## Start here

1. [Architecture](architecture.md)
2. [Configuration](reference/configuration.md)
3. [Runtime profile matrix](reference/profile-matrix.md)
4. [Backend API](reference/http-api.md)
4. [CLI](reference/cli.md)
5. [Terminal UI](how-to-guides/terminal-tui.md)
6. [Testing strategy](how-to-guides/testing-strategy.md)
7. [DSPy RLM and Daytona integration](how-to-guides/dspy-integration.md)
8. [Daytona Snapshot](how-to-guides/daytona-snapshot.md)
9. [Official Oolong benchmark](how-to-guides/official-oolong.md)
10. [Evaluation, monitoring, and signature optimization](how-to-guides/evaluation-optimization.md)
11. [Agent harness](agent-harness/README.md)

## Reference

- [Complete table of contents](SUMMARY.md)
- [Reference index](reference/index.md)
- [Codebase map](reference/codebase-map.md)
- [Source layout](reference/source-layout.md)
- [Database](reference/database.md)

## Source of truth

- backend: `src/fleet_rlm/`
- terminal: `tools/fleet-tui/`
- HTTP contract: `openapi.yaml`
- generated TUI HTTP types: `tools/fleet-tui/src/generated/openapi.ts`
- schema: `migrations/`
- validation: `Makefile`, `tests/`, and TUI tests

No tracked implementation plan is currently active. Local `PLANS.md` is a
phase-verification record; ignored `.scratch/archive/` contains noncanonical
historical plans/evidence.
