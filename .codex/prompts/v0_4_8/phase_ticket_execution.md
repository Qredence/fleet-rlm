# Phase Ticket Execution Runbook

Use the `lead` role to coordinate ticket execution within a phase.

## Deterministic Execution Pattern
For each ticket (or tightly coupled bundle):
1. Use the `explorer` role for impact map (files, imports, tests, docs).
2. Use the `backend_impl` or `frontend_impl` role to implement changes.
3. Run ticket-scoped validation (tests/lint/type/security as applicable).
4. Commit only after validation passes or skips are documented.
5. Use the `linear_ops` role to post a progress comment with results and blockers.

## Parallelization Rules
- Parallelize only when file overlap is low and the critical path is preserved.
- Serialize Alembic migration generation and shared core component edits.
- If two tickets overlap heavily, assign one owner and sequence the other.

## Negative Routing Examples
- Do not use `linear_ops` to decide code architecture.
- Do not use `qa_playwright` to replace unit/integration tests.
