# Fleet CLI Supervision and Daytona Diagnostics

## Purpose

Make `fleet cli` and `fleet deno` supervise the selected backend plus the Ink
terminal, add an opt-in disposable Daytona doctor, and preserve coarse public
Turn errors while exposing safe correlated diagnostics.

## Progress

- [x] Supervise backend readiness, Ink launch, signals, logs, and cleanup.
- [x] Add `fleet doctor daytona` with guaranteed disposable Sandbox cleanup.
- [x] Classify and correlate Turn-preparation failures with bounded safe retry.
- [x] Show correlation metadata in Ink and align active documentation.
- [x] Complete focused, full, Deno, docs, API, and drift validation.
- [ ] Complete live Daytona Sandbox/Turn acceptance (blocked by provider disk quota).
- [ ] Remove this temporary plan after durable documentation is current.

## Decisions

- `fleet cli` means Daytona backend plus Ink; `fleet deno` means Deno backend
  plus Ink. Backend-only launch remains `fleet web` or `fleet-rlm serve-api`.
- Daytona validation is opt-in through `fleet doctor daytona`.
- Public 503 bodies stay coarse; only correlation metadata and sanitized server
  diagnostics deepen.
- A disposable doctor Sandbox is always deleted in `finally` and never creates
  Fleet domain rows or bindings.

## Validation Notes

- `make check`, `make test-deno`, `make api-check`, `make check-docs`,
  `make check-codebase-tree`, and `git diff --check` pass.
- The live doctor confirms settings, database/Alembic compatibility, Daytona
  authentication, and Volume visibility. Daytona then rejects Sandbox creation
  because the organization has exceeded its total disk capacity. The doctor
  correctly reports `quota` and the required provider-account action.
- A Daytona Turn and combined `fleet cli` prompt remain externally blocked
  until unused Sandboxes are archived/deleted or the account tier is raised.

## Completion Gate

```bash
uv run fleet doctor daytona
make check
make test-deno
make api-check
make check-docs
make check-codebase-tree
git diff --check
```
