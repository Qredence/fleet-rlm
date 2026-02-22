# Phase Docs + Hygiene Runbook

Use the `docs_keeper` role after implementation stabilizes and before PR open.

## Required Updates (Every Phase)
- Update `@plan/implementation-0.4.8/README.md` phase status
- Write/update phase outcome log in `@plan/implementation-0.4.8/phase-logs/`
- Update `/Users/zocho/.codex/worktrees/d075/fleet-rlm-dspy/AGENTS.md` if workflow/architecture/tooling changed
- Update `docs/` when user-visible behavior or operational guidance changed
- Verify imports and stale symbol/path references after refactors/removals

## Continuity Requirement
Phase outcome log must include:
- what shipped
- validation evidence
- Playwright artifacts
- Linear updates
- remaining risks / next-phase prerequisites

## Negative Routing Examples
- Do not leave stable workflow conventions only in chat; capture them in `AGENTS.md`.
- Do not postpone phase outcome logging until after the next phase starts.
