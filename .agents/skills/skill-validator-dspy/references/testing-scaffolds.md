# Validator Testing Scaffolds

Use this when writing unit or integration tests for validators.

## Unit Tests

- Test metric normalization and weighting in isolation.
- Use small fixtures for SKILL.md with known outcomes.
- Avoid LLM calls; enable deterministic fallbacks.

## Repo Anchors (this repo)

- Rule-based validator tests: `tests/unit/test_skill_validator.py`
- Best-of-N + adaptive validator tests: `tests/unit/core/modules/validation/test_best_of_n_validator.py`
- DSPy reward utilities: `tests/unit/core/test_dspy_utils.py`
- End-to-end validation workflow: `tests/integration/test_enhanced_validation.py`

## Integration Tests

- Build a fixture directory under `tests/fixtures/skills/...`.
- Validate full flow: parse -> metrics -> weighting -> verdict.
- Assert errors and warnings are collected, not just thrown.

## Example Fixture Pattern

```
fixtures/
  skills/
    minimal-skill/
      SKILL.md
      references/
```

## Test Tips

- Use parametrized tests for multiple skill layouts.
- Explicitly set `SKILL_FLEET_ALLOW_LLM_FALLBACK=1` where needed.
