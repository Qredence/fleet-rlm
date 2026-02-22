# Phase Close + Next Branch Runbook

Use the `lead` role to close a phase after human-approved merge.

## Close Phase (Post-Merge)
1. Pull latest `main`.
2. Run a lightweight post-merge smoke/verification.
3. Use the `linear_ops` role to:
   - move merged phase tickets to `Done`
   - post final merge comments (PR URL + merged commit + final notes)
   - remove `status: needs-review` label if used
   - post project status update summarizing completed phase and next phase readiness
4. Use the `docs_keeper` role to finalize the phase outcome log and update `@plan/implementation-0.4.8/README.md` status to `Merged`.

## Next Branch Rule (Merge-Then-Next)
Create the next phase branch only after the previous phase PR is merged and post-merge checks complete.

## Negative Routing Examples
- Do not start the next phase on top of an unmerged previous phase branch.
- Do not mark tickets `Done` at PR-open time.
