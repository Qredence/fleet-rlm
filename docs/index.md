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
8. [Structural reduction plan](testing/structural-reduction-plan.md)
9. [DSPy RLM and Daytona integration](how-to-guides/dspy-integration.md)
10. [Daytona Snapshot](how-to-guides/daytona-snapshot.md)
11. [Evaluation and monitoring](how-to-guides/evaluation-optimization.md)

## Current runtime and active migration

The supported runtime boundary and ownership map are maintained in
[ARCHITECTURE.md](../ARCHITECTURE.md). Validation lanes and evidence limits are
defined by the [testing strategy](how-to-guides/testing-strategy.md).

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
- [P41 behavior freeze](reference/behavior-freeze.md)

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

The maintained architecture, testing strategy, and performance budget track
current implementation ownership, validation scope, and dated measurements.
Historical baselines preserve their original evidence scope and do not override
current code, policy, or generated contracts.
