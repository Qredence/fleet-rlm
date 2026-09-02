# Documentation Home

Fleet RLM has one Python backend under `src/fleet_rlm/` and one maintained
development client under `tools/fleet-tui/`. It exposes a compact Session-first
FastAPI/SSE contract backed by DSPy, Daytona, and SQLAlchemy/Alembic.

## Start here

1. [Architecture](../ARCHITECTURE.md)
2. [Configuration](reference/configuration.md)
3. [Runtime profile matrix](reference/profile-matrix.md)
4. [Backend API](reference/http-api.md)
5. [CLI](reference/cli.md)
6. [Terminal UI](how-to-guides/terminal-tui.md)
7. [Testing strategy](how-to-guides/testing-strategy.md)
8. [DSPy RLM and Daytona integration](how-to-guides/dspy-integration.md)
9. [Daytona Snapshot](how-to-guides/daytona-snapshot.md)
10. [Evaluation and monitoring](how-to-guides/evaluation-optimization.md)
11. [Maintainability freeze](how-to-guides/maintainability-freeze.md)
12. [P35-D callback observability decision](how-to-guides/p35d-callback-observability-decision.md)
13. [P36 ownership and deletion contract](how-to-guides/p36-ownership-deletion-inventory.md)
14. [P41 behavior freeze](reference/behavior-freeze.md)
15. [P42 Session-state behavior freeze](reference/p42-session-state-behavior-freeze.md)
16. [P42 module-subtraction ledger](reference/p42-module-subtraction-ledger.md)
17. [Session-scoped RLM state ADR](decisions/ADR-session-scoped-rlm-state.md)

## Reference

- [Complete table of contents](SUMMARY.md)
- [Reference index](reference/index.md)
- [Source layout](reference/source-layout.md)
- [Database](reference/database.md)

## Source of truth

- backend: `src/fleet_rlm/`
- terminal: `tools/fleet-tui/`
- HTTP contract: `openapi.yaml`
- generated TUI HTTP types: `tools/fleet-tui/src/generated/openapi.ts`
- schema: `migrations/`
- validation: `Makefile`, `tests/`, and TUI tests

No tracked implementation plan is currently active; internal mission plans
and ExecPlan guides are kept under the ignored local `.scratch/` tree.
