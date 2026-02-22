# PR + Linear Sync Runbook

Use the `lead` role to prepare PR creation. Use the `linear_ops` role for all Linear mutations.

## PR Open Sequence
1. Confirm validation gate passed (including reviewer + Playwright smoke).
2. Push branch.
3. Create PR with phase summary, ticket list, validation evidence, docs impact, and risks.
4. Use the `linear_ops` role to:
   - post PR URL + validation summary on every included ticket
   - add label `status: needs-review`
   - post/update project status update for phase in review

## Linear Policy
- Keep tickets `In Progress` while PR is open
- Move to `Done` only after merge + post-merge validation

## Negative Routing Examples
- Do not use `backend_impl` or `frontend_impl` to post PR links/comments in Linear.
- Do not create a PR before Playwright smoke evidence is recorded.
