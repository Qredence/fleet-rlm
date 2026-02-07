# Adaptive Metric Weighting

Use this when implementing or tuning metric weighting.

## Principles

- Normalize metric scores to a common scale (0..1).
- Keep weight policy separate from metric computation.
- Allow overrides via config (env or config file).

## Example Policy

```python
weights = {
    "structure": 0.35,
    "compliance": 0.35,
    "quality": 0.30,
}

score = sum(metrics[k] * weights[k] for k in weights)
```

## Adaptive Adjustment

- If a required section is missing, cap max score or set a penalty.
- If deterministic checks fail, skip DSPy weighting and return a lower bound.
- Keep adaptive changes transparent: include in result metadata.

## Repo Anchors (this repo)

- Adaptive validator: `src/skill_fleet/core/modules/validation/adaptive_validator.py`
- Best-of-N validator: `src/skill_fleet/core/modules/validation/best_of_n_validator.py`
- Reward/selection logic: `src/skill_fleet/core/modules/validation/validation_reward.py`
- Metric collection: `src/skill_fleet/core/modules/validation/metrics.py`
