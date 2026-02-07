---
name: skill-validator-dspy
description: Design and test Skill validators using the DSPy core approach. Use when creating or refactoring validation logic, adaptive metric weighting, compliance checks, or test scaffolds for skills and SKILL.md artifacts in this repo. Triggers on: "skill validator", "adaptive metric weighting", "validator tests", "compliance checker", "DSPy validation".
---

# Skill Validator Design and Testing (DSPy Core)

Use this skill for validator design, metric weighting, and testing, aligned with DSPy modules and the repo’s validator patterns.

## Triage

Identify which task applies and load only the needed references:

- **Design/Refactor validator modules**: Load `references/validator-design.md`.
- **Adaptive metric weighting**: Load `references/metric-weighting.md`.
- **Unit/integration tests for validators**: Load `references/testing-scaffolds.md`.
- **DSPy module patterns** (if unclear): Load `references/dspy-core.md`.

Do NOT load other references unless required.

## Core Workflow

1. **Locate the validator boundary**
   - Identify where validation happens (service layer, validators package, CLI).
   - Confirm the expected inputs/outputs (skill path, SKILL.md content, metrics).

2. **Align with DSPy core patterns**
   - Use `BaseModule` + `aforward()` for async flow and deterministic structure.
   - Prefer signature-driven inputs/outputs where a DSPy module is appropriate.

3. **Model the metrics**
   - Define metric inputs, normalization, and weighting policy.
   - Separate metric computation from aggregation/weighting.

4. **Implement safe defaults**
   - Deterministic behavior if LLM is unavailable.
   - Clear fallbacks for missing files or malformed frontmatter.

5. **Add tests**
   - Unit tests for pure metric logic and weighting.
   - Integration tests for validator orchestration and file parsing.

6. **Explain the behavior**
   - Provide a crisp summary of metric math and edge cases.
   - Include example outputs with known fixtures.

## Repo Anchors (this repo)

- `src/skill_fleet/validators/skill_validator.py`
- `src/skill_fleet/core/modules/validation/`
- `src/skill_fleet/core/workflows/skill_creation/validation.py`

## Anti-Patterns (Never Do)

- Never embed business logic in route handlers; keep it in validator modules/services.
- Never mix metric computation and policy (weighting) in the same function.
- Never introduce non-deterministic behavior in validator tests.
- Never let missing files crash validation without a controlled error path.
- Never rely on implicit global config in tests; use explicit test fixtures.

## Output Expectations

- Show a clear validator flow (inputs -> metrics -> weighting -> verdict).
- Provide test scaffolds and sample fixtures.
- Keep DSPy integration explicit and minimal.
