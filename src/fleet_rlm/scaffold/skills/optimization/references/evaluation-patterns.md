# Evaluation Patterns

Patterns for designing scorers, composing metrics, and running evaluations against DSPy modules in fleet-rlm.

---

## Scorer Design

Every scorer implements a `__call__` method returning a float between 0.0 and 1.0:

```python
class MyScorer:
    """Score prediction quality on a 0-1 scale."""

    def __call__(self, example, prediction) -> float:
        # example: the input + expected output (dspy.Example)
        # prediction: the module's actual output (dspy.Prediction)
        if prediction.answer == example.expected_answer:
            return 1.0
        return 0.0
```

**Conventions:**
- Return `1.0` for perfect, `0.0` for complete failure
- Intermediate values for partial credit
- Raise `ScorerError` (not generic exceptions) for unscoreable inputs
- Include a `name` property for MLflow logging

---

## Built-in Scorers

| Scorer | What it measures | Config |
|--------|-----------------|--------|
| `SafetyScorer` | Output safety (toxicity, PII, prompt injection) | `safety_threshold=0.9` |
| `GuidelineAdherenceScorer` | Whether output follows specified guidelines | `guidelines: list[str]` |
| `LLMJudgeScorer` | Custom LLM-as-judge evaluation | `judge_prompt`, `judge_model` |
| `ExactMatchScorer` | Exact string match against expected | — |
| `ContainsScorer` | Output contains required substrings | `required: list[str]` |
| `JSONValidScorer` | Output is valid JSON matching schema | `schema: dict` |

---

## Metric Composition with `dspy_evaluation.py`

The `evaluate_module()` function runs multiple scorers and aggregates results:

```python
from fleet_rlm.quality.dspy_evaluation import evaluate_module

results = evaluate_module(
    module=my_module,
    dataset=dev_examples,
    scorers=[SafetyScorer(), GuidelineAdherenceScorer(guidelines=my_guidelines)],
    aggregate="weighted_mean",  # or "min", "mean", "product"
    weights={"safety": 0.3, "guideline_adherence": 0.7},
)

print(results.aggregate_score)   # 0.0 - 1.0
print(results.per_scorer)        # {"safety": 0.95, "guideline_adherence": 0.82}
print(results.per_example)       # List of per-example breakdowns
```

**Aggregation strategies:**
- `mean` — simple average across scorers
- `weighted_mean` — weighted by importance (supply `weights` dict)
- `min` — worst scorer determines the score (strict)
- `product` — multiply all scores (penalizes any weakness)

---

## Auto-Assessment

Enable auto-assessment to score every RLM execution in production:

```bash
# Environment variables
FLEET_RLM_ENABLE_AUTO_ASSESSMENT=true
FLEET_RLM_AUTO_ASSESSMENT_SCORERS=safety,guideline_adherence
```

| Variable | Description | Default |
|----------|-------------|---------|
| `FLEET_RLM_ENABLE_AUTO_ASSESSMENT` | Enable scoring on every execution | `false` |
| `FLEET_RLM_AUTO_ASSESSMENT_SCORERS` | Comma-separated scorer names | `safety` |
| `FLEET_RLM_AUTO_ASSESSMENT_SAMPLE_RATE` | Fraction of executions to score | `1.0` |
| `FLEET_RLM_AUTO_ASSESSMENT_ASYNC` | Run scoring asynchronously | `true` |

Auto-assessment results are logged to MLflow as trace attributes and available via:
```bash
curl -X GET "/api/v1/optimization/assessments?module=grounded_answer&since=2024-01-01"
```

---

## Trace-Aware Metrics

Score based on the full execution trace, not just the final output. Useful for evaluating reasoning quality, tool usage, and intermediate steps.

```python
class TraceAwareScorer:
    """Score based on execution trace, not just final output."""

    def __call__(self, example, prediction) -> float:
        trace = prediction.trace  # Full execution trace

        # Check that the module used tools appropriately
        tool_calls = [step for step in trace if step.type == "tool_call"]
        if len(tool_calls) == 0 and example.requires_tools:
            return 0.2  # Penalize skipping required tools

        # Check reasoning chain quality
        reasoning_steps = [step for step in trace if step.type == "reasoning"]
        if len(reasoning_steps) < 2:
            return 0.5  # Penalize shallow reasoning

        # Final answer correctness
        if prediction.answer == example.expected_answer:
            return 1.0

        return 0.3
```

**Trace fields available:**
- `trace.steps` — ordered list of execution steps
- `step.type` — `"reasoning"`, `"tool_call"`, `"tool_result"`, `"delegation"`
- `step.content` — the actual content of that step
- `step.duration_ms` — time taken
- `step.token_count` — tokens consumed

---

## Custom LLM Judge

For nuanced quality assessment, use an LLM as judge:

```python
from fleet_rlm.quality.scorers import LLMJudgeScorer

judge = LLMJudgeScorer(
    judge_prompt="""
    Rate the following answer on a scale of 0 to 10 for:
    1. Accuracy: Does it correctly answer the question?
    2. Completeness: Does it cover all aspects?
    3. Conciseness: Is it appropriately brief?

    Question: {question}
    Expected: {expected_answer}
    Actual: {answer}

    Return a JSON object: {"accuracy": N, "completeness": N, "conciseness": N}
    """,
    judge_model="anthropic/claude-sonnet-4-20250514",
    normalize=True,  # Convert 0-10 to 0.0-1.0
)
```

---

## Evaluation Workflow

```bash
# Run evaluation on a module with all registered scorers
uv run fleet-rlm evaluate grounded_answer ./data/dev.jsonl --all-scorers

# Run with specific scorers only
uv run fleet-rlm evaluate grounded_answer ./data/dev.jsonl \
    --scorers safety,guideline_adherence

# Generate comparison report between two prompts
uv run fleet-rlm evaluate grounded_answer ./data/dev.jsonl \
    --compare-with .fleet_rlm/optimized/grounded_answer_v1.json \
    --report
```
