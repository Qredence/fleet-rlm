# Domain documentation

Fleet RLM uses a multi-context glossary layout.

## Reading rules

1. Read `CONTEXT-MAP.md` and the context relevant to the work.
2. Use glossary terms consistently in issues, plans, tests, and review findings.
3. Keep `CONTEXT.md` files free of implementation plans and code-level workflow detail.

## Contexts

- `CONTEXT.md` contains concepts shared across the whole product.
- `src/fleet_rlm/CONTEXT.md` contains the canonical RLM-native backend language
  (Turns, Runs, leases, Skill Cards, staged Attachments, and Artifact Candidates).

ADRs for the deleted backend are preserved under
`docs/internal/legacy-backend/adr/` as history and are not current authority.
Record new hard-to-reverse decisions alongside the active planning artifact.
