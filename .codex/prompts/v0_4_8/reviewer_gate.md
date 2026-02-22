# Reviewer Gate Prompt

Use the `reviewer` role before PR open on multi-ticket phases.

## Review Objective
Produce a findings-first review focused on:
- behavioral regressions
- correctness bugs
- missing tests / incomplete validation
- security or privacy regressions
- risky assumptions and edge-case gaps

## Output Format
1. Findings (ordered by severity, with file references)
2. Open questions / assumptions
3. Residual risks and validation gaps
4. Short summary only after findings

## Negative Routing Examples
- Do not spend the review restating the implementation when there are actionable findings.
- Do not approve by default without checking tests and edge cases.
