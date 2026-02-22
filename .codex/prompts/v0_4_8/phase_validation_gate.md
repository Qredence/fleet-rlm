# Phase Validation Gate

Use the `lead` role to run the gate, and explicitly invoke specialist roles for each step.

## Required Gate (Before PR Open)
1. Use the `reviewer` role to perform a findings-first review of the current diff.
2. Run phase-appropriate checks (format, lint, typecheck, tests, security).
3. Use the `qa_playwright` role to run browser smoke validation and capture artifacts.
4. Use the `docs_keeper` role to verify docs/import/reference hygiene updates are complete.

## Validation Recording
Record exact commands, passes/failures/skips, and artifact paths in:
- PR body
- phase outcome log (`@plan/implementation-0.4.8/phase-logs/`)
- relevant Linear comments

## Negative Routing Examples
- Do not skip `reviewer` on multi-ticket phases.
- Do not mark Linear tickets `Done` before merge even if all checks pass.
