# Linear Comment Templates (Use via `linear_ops` Role)

Use the `linear_ops` role to post these comments. Do not post from implementation roles.

## Progress Checkpoint Template
```md
Progress checkpoint on `<branch>`:

Completed:
- <implemented change>
- <implemented change>

Validation run:
- `<command>` -> <pass/fail/skip>
- `<command>` -> <pass/fail/skip>

Blockers / risks:
- <none | issue>

Next:
- <next step>
```

## PR Open Template
```md
PR opened: <PR URL>

Included scope:
- <tickets or sub-scope>

Validation summary:
- <tests/lint/type/security summary>
- Playwright smoke: <pass/fail> (artifacts: `output/playwright/phase-XX/`)

Docs/hygiene updates:
- <AGENTS.md / @plan / docs paths>

Ticket remains `In Progress` until merge per phase policy.
```

## Merge Completion Template
```md
Merged: <PR URL>

Post-merge verification:
- `<command>` -> <result>

Final notes:
- <important caveat or none>

Marking ticket `Done` after merge and post-merge validation per phase policy.
```

## Project Status Update Template (Summary)
```md
Phase <N> status: <In Review | Merged>

Completed this phase:
- <high-level outcomes>

Validation evidence:
- <checks>
- Playwright artifacts: `output/playwright/phase-XX/`

Next phase readiness:
- <ready / blockers>
```
