# DSPy Core Alignment

Use this when implementing validation logic that should align with DSPy patterns.

## Core Pattern

- Validators that require LLM reasoning should be DSPy modules.
- Async-first: implement `aforward()`; sync wrapper via `forward()` in `BaseModule`.
- Use clear, minimal signatures for deterministic inputs/outputs.

## Repo Anchors (this repo)

- Base module pattern: `src/skill_fleet/core/modules/base.py`
- Validation modules: `src/skill_fleet/core/modules/validation/`

## Example Signature

```python
import dspy

class ValidateSkill(dspy.Signature):
    skill_content: str = dspy.InputField()
    requirements: list[str] = dspy.InputField()

    issues: list[str] = dspy.OutputField()
    score: float = dspy.OutputField()
    reasoning: dspy.Reasoning = dspy.OutputField()
```

## Guidance

- Keep business logic outside DSPy modules (parse files, compute base metrics).
- Use DSPy only where model judgment is required.
- Provide deterministic fallbacks for tests (e.g., `SKILL_FLEET_ALLOW_LLM_FALLBACK`).
