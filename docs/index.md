# Documentation Home

Fleet RLM has one Python backend under `src/fleet_rlm/` and one maintained
development client under `tools/fleet-tui/`. It exposes a compact Session-first
FastAPI/SSE contract backed by DSPy, Daytona, and SQLAlchemy/Alembic.

The current selectable runtime is `legacy`, using native DSPy RLM with resident
Session reuse. The native Daytona adapter is experimental. Guides describe
current behavior; decision records and plans distinguish targets from shipped
contracts and dated validation evidence.

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

## Runtime decisions and active migration

- [Session-scoped RLM state ADR](decisions/ADR-session-scoped-rlm-state.md) — current legacy behavior.
- [Turn interpreter context target (ADR 004)](decisions/004-turn-interpreter-context.md) — gated target.
- [Runtime variant (ADR 005)](decisions/005-runtime-variant.md) — selectable policy contract.
- [Native runtime and MLflow evidence (ADR 006)](decisions/006-native-turn-scoped-runtime-and-evaluation.md) — proposed architecture and gates.
- [ADR 006 implementation status](decisions/006-implementation-status.md) — dated results and remaining work.
- [ADR 006 consolidated implementation plan](../fleet-rlm-implementation-plan-2026-09-06-v2.md) — detailed task ledger.

## Historical baselines

- [Maintainability freeze](how-to-guides/maintainability-freeze.md)
- [P35-D callback observability decision](how-to-guides/p35d-callback-observability-decision.md)
- [P36 ownership and deletion contract](how-to-guides/p36-ownership-deletion-inventory.md)
- [P41 behavior freeze](reference/behavior-freeze.md)
- [P42 Session-state behavior freeze](reference/p42-session-state-behavior-freeze.md)
- [P42 module-subtraction ledger](reference/p42-module-subtraction-ledger.md)

## Reference

- [Complete table of contents](SUMMARY.md)
- [Reference index](reference/index.md)
- [Source layout](reference/source-layout.md)
- [Database](reference/database.md)

## Source of truth

Repository-wide Markdown includes root contributor/governance documents, these
guides and records, script/migration/TUI READMEs, GitHub templates, and bundled
runtime Skills. Preserve release history and dated evidence; correct current
guidance against its owning implementation. Generated Markdown, including the
profile matrix, must be regenerated from its source rather than edited by hand.

- backend: `src/fleet_rlm/`
- terminal: `tools/fleet-tui/`
- HTTP contract: `openapi.yaml`
- generated TUI HTTP types: `tools/fleet-tui/src/generated/openapi.ts`
- schema: `migrations/`
- validation: `Makefile`, `tests/`, and TUI tests

The maintained [ADR 006 implementation plan](../fleet-rlm-implementation-plan-2026-09-06-v2.md)
and its [execution/status ledger](decisions/006-implementation-status.md) track
implementation through Recursive RLM v2 separately from live certification and
rollout authorization. Older scratch roadmaps remain historical references.
