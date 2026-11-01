# Agent review checklist — version 1

- Is the change tied to a named failure or release intent, with original evidence?
- Does the regression test demonstrate the failure and the corrected behavior?
- Are the existing 80% coverage floor, Python matrix, quality status, and publication
  gates intact? Do the checks cover behavior rather than merely mirror new code?
- Does the change respect the architecture and runtime/evaluation guardrails in
  `AGENTS.md`? Identify runtime ownership, cancellation, provider, persistence,
  provenance, or API changes for explicit human scrutiny.
- Are logs/artifacts treated as untrusted data and sensitive content excluded from
  the PR? Are secrets and publishing permissions untouched?
- Is the PR small enough to review as one change? Separate unrelated refactoring.
- Does the description link the original failure and validation runs, explain what
  changed and why, and state material uncertainty and rollback steps?
- For releases: do notes, source SHA, distribution manifest, and canary receipt agree?
  A green timed validation window alone is not proof of successful package checks.

Human review and merge remain required. A successful retry is flaky-test evidence
to investigate, not proof that a race is harmless. Feed confirmed failure patterns
back into regression tests or these versioned prompts in a separate reviewed change.
