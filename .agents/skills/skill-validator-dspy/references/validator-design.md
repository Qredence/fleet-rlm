# Validator Design

Use this for designing or refactoring validators.

## Layering

- **Parsing**: Read and validate SKILL.md frontmatter.
- **Structure**: Validate directory layout and required sections.
- **Compliance**: Apply agentskills.io rules.
- **Quality**: Use DSPy modules when judgment is needed.

## Repo Anchors (this repo)

- Rule-based validator: `src/skill_fleet/validators/skill_validator.py`
- DSPy validation modules: `src/skill_fleet/core/modules/validation/`
- Validation workflow orchestration: `src/skill_fleet/core/workflows/skill_creation/validation.py`
- Validation report models: `src/skill_fleet/core/workflows/models.py`

## Recommended Flow

1. Parse inputs + filesystem layout.
2. Compute deterministic metrics.
3. Apply weighting/policy.
4. If configured, run DSPy module for judgment-based checks.
5. Return consolidated verdict.

## Error Handling

- Use typed exceptions for user-fixable errors vs internal errors.
- Aggregate issues; avoid early exits unless the input is invalid.
