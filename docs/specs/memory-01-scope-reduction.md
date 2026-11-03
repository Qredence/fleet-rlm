# Phase 01: scope reduction

Status: implemented, 2026-11-03. Additive phases remain deferred. See the
[program index](memory-program.md).

## Purpose

Remove the uncommitted managed-memory experiment from the public runtime while
preserving existing local memories and database compatibility. Remote recall,
Jev memory selection, automatic conversation export, Dreamer pipelines, and
operator controls did not establish a demonstrated improvement to Fleet.

## Retained

- Existing Session-scoped memory CRUD, reviewed-memory context, history,
  routing, sandbox lifecycle, and native DSPy evaluation and optimization.
- Migrations 9 and 10, unchanged, including their tables and existing rows.
  Databases already at version 10 must remain readable. The tables are inert;
  initialization preserves their migration history without starting workers.
- Optional Lakebase OAuth and Databricks MLflow support that predates this work.
  Authentication uses the existing SDK/environment configuration. No personal
  profile default or new identity setting is introduced.
- The small `[mlflow].mode` TOML improvement. Explicit CLI selection wins;
  managed optimizer workers inherit their server settings. Interactive terminal
  and browser TUI launchers can still open setup when configuration is invalid.
- Every document under `docs/specs`; additive features remain proposals.

## Removed or reverted

- Managed-memory service, selector, immutable Session bindings, SQL outbox
  operations, exporter lifecycle, pipeline ledger methods and admission leases.
- Operator API and CLI commands, managed health and Turn fields, trace receipts,
  TUI status panels, and the selector-only budget reservation method.
- The managed-only Turn purpose field and its evaluator plumbing.
- Managed settings, resource IDs, profile plumbing, and model-default changes.
  Existing private TOML must remove `[managed_memory]`; unsupported tables
  continue to produce an actionable configuration error.
- Expanded database-readiness privilege checks and their tests. They included
  an incorrect schema-owner requirement. The original read-only setup probe
  remains; a richer database-only workflow is deferred to Phase 02.
- Dedicated managed-memory tests, live verifiers, their direct `requests`
  dependency, provider-specific runbooks, and the local progress tracker.
- Managed-memory additions to active API documentation and architecture.
  Existing optional database/provider documentation stays generic.

## Public interfaces and user impact

Terminal and browser-hosted TUI use the same existing Session API. `/memory`
continues to inspect local Session memories. Remote recall, exports, and
pipeline controls are absent. No new learning endpoint is added.

The OpenAPI contract returns to the existing local-memory surface. Existing
local-memory requests and Turn results remain unchanged. The public config
contains no new personal profile, pipeline ID, or forced model change.

No database or remote resource is deleted. Retaining migration SQL is a
compatibility measure, not a promise to resume the removed experiment.

## Verification

- Offline regression coverage for TOML backend selection, explicit overrides,
  and invalid-configuration onboarding in both `fleet cli` and `fleet web`.
- A version-10 database starts without reapplying migrations or accessing the
  inert managed tables. Existing Store coverage checks older/future versions.
- One final `make check` covers tests, lint, formatting, types, documentation,
  the generated OpenAPI contract, and the package build.
- Live model, Daytona, database, and evaluation runs are excluded.

## Deferred work

Phases 00, 02, 03, and 04 require separate review. This cleanup does not add
contract models, a database CLI, source-link tables, or draft-learning calls.
