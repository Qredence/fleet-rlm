# Documentation Home

Fleet RLM has one Python backend under `src/fleet_rlm/` and one maintained
development client under `tools/fleet-tui/`. It exposes a compact Session-first
FastAPI/SSE contract backed by DSPy, Daytona, and SQLAlchemy/Alembic.

Fleet uses native DSPy RLM with a fresh program per Run. Sequential Turns may
reuse a healthy Root Sandbox, which receives submitted Python source and
serializable values. A Sandbox-local broker dispatches authorized Fleet and
DSPy semantic tools to the host over Daytona's authenticated preview connection,
so host callables and preview credentials stay on the host. Native Daytona
interpreter cutover is not selected.

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
11. [Phase 6 promotion and rollback](how-to-guides/phase6-promotion.md)

## Current runtime and active migration

- [Session-scoped RLM state ADR](decisions/ADR-session-scoped-rlm-state.md) — historical Session-state decision; current ownership is recorded in ADR 006.
- [Turn interpreter context target (ADR 004)](decisions/004-turn-interpreter-context.md) — gated target.
- [Retired runtime selector (ADR 005)](decisions/005-runtime-variant.md) — historical migration contract.
- [Native runtime and MLflow evidence (ADR 006)](decisions/006-native-turn-scoped-runtime-and-evaluation.md) — retained-broker architecture, historical target decisions, and open gates.
- [ADR 006 implementation status](decisions/006-implementation-status.md) — dated results and verification ledger.

The configured Daytona path reuses a healthy Root Sandbox for submitted source
and serializable bindings, resetting the execution namespace and invocation
credential between Turns. Authorized Fleet tools and DSPy's native semantic
tools reach the host through the same broker path. Live recursive execution and
trace retrieval are verified by the maintained recursive-batch canary; this does
not certify recursive value. Phase 3 complete-MVP and Phase 5–6 operational
certification are open. Read the status ledger before treating any dated receipt
as a current guarantee.

## Historical baselines and evidence

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
- [Workspace Agent filesystem operation audit](reference/workspace-agent-operation-audit.md)

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

The maintained [ADR 006 implementation status ledger](decisions/006-implementation-status.md)
tracks implementation separately from live certification and rollout authorization.
Historical baselines preserve their original evidence scope and do not override
current code, policy, or generated contracts.
