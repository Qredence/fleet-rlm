# v0.4.8 Phase Control (Lead Conductor)

Use the `lead` role to orchestrate an entire milestone phase from kickoff to PR handoff.

## Start-of-Phase Inputs
- Read `@plan/implementation-0.4.8/README.md`
- Read the previous phase outcome log in `@plan/implementation-0.4.8/phase-logs/`
- Read the relevant group analysis file(s) in `@plan/implementation-0.4.8/`
- Confirm active branch and target phase ticket list

## Deterministic Role Routing
- Use the `linear_ops` role for all Linear label/state/cycle/comment/status changes.
- Use the `explorer` role for repo mapping and impact analysis before edits.
- Use the `backend_impl` / `frontend_impl` role for implementation work.
- Use the `reviewer` role before PR open on multi-ticket phases.
- Use the `qa_playwright` role for browser smoke/regression validation.
- Use the `docs_keeper` role to update docs, `AGENTS.md`, `@plan`, and phase outcome logs.

Do not use the `lead` role as a catch-all implementer when a specialist role fits.
Do not use `linear_ops` for code edits or `qa_playwright` for implementation.

## Phase Loop
1. Linear kickoff + issue state sync
2. Exploration and dependency confirmation
3. Ticket implementation in planned order (parallel only where safe)
4. Validation gate (tests/lint/type/security + reviewer)
5. Playwright smoke validation
6. Docs + hygiene sync
7. Commit/push/PR + Linear PR comments + review label
8. Human review/merge checkpoint
9. Post-merge validation + Linear Done + phase outcome log finalization
10. Create next phase branch (merge-then-next)

## Artifact Boundaries
- Browser artifacts: `output/playwright/phase-XX/`
- Phase summaries/handoffs: `@plan/implementation-0.4.8/phase-logs/`

## Continuity Rule
Every phase must end with a compact handoff summary in a phase outcome log. The next phase must begin by reading that log.
