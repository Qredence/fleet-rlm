# Phase Start Runbook

Use the `lead` role to start a phase.

## Checklist
1. Confirm branch name follows `codex/v0-4-8-phase-<n>-<slug>` (Phase 0 bootstrap may use `codex/v0-4-8-phase-0-bootstrap`).
2. Read `@plan/implementation-0.4.8/README.md` and prior phase log.
3. Use the `linear_ops` role to:
   - list phase tickets
   - verify milestone `v0.4.8`, phase label, and cycle
   - move active tickets to `In Progress`
   - post kickoff comment on the phase anchor ticket
4. Use the `explorer` role to map likely files/tests/docs touched.

## Negative Routing Examples
- Do not use `reviewer` at phase start unless reviewing an existing diff.
- Do not use `qa_playwright` before the phase has runnable changes to validate.
