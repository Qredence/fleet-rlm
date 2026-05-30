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
| `RelevanceToQuery` | Whether the final answer solves the user's query | `model` |
| `ToolCallCorrectness` | Whether tool calls match the available schemas | `model` |
| `ToolCallEfficiency` | Whether the agent avoids redundant tool calls | `model` |
| `RetrievalGroundedness` | Whether retrieved context supports the answer | `model` |
| `reasoning_quality_scorer` | Optional LLM judge for reasoning trace quality | `model` |

---

## DSPy Evaluation with `dspy_evaluation.py`

The `evaluate_program()` function runs a DSPy program against a devset with a single DSPy-compatible metric:

```python
from fleet_rlm.quality.dspy_evaluation import evaluate_program
from fleet_rlm.quality.workspace_metrics import workspace_score_metric

results = evaluate_program(
    program=my_program,
    devset=dev_examples,
    metric=workspace_score_metric,
    return_all_scores=True,
    return_outputs=True,
)

print(results["score"])          # 0.0 - 1.0
print(results["all_scores"])     # Per-example scores
print(results["outputs"])        # Per-example predictions
```

For exported trace datasets, use `evaluate_program_from_dataset()` to load rows and coerce them into `dspy.Example` objects before evaluation.

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
        spans = trace.search_spans()
        tool_calls = [span for span in spans if getattr(span, "span_type", getattr(span, "type", "")) == "tool"]
        if len(tool_calls) == 0 and example.requires_tools:
            return 0.2  # Penalize skipping required tools

        # Check reasoning chain quality
        reasoning_steps = [
            span
            for span in spans
            if str(getattr(span, "name", "")).lower().startswith(("thought", "llm"))
        ]
        if len(reasoning_steps) < 2:
            return 0.5  # Penalize shallow reasoning

        # Final answer correctness
        if prediction.answer == example.expected_answer:
            return 1.0

        return 0.3
```

**Trace fields available:**
- `trace.search_spans()` — ordered MLflow trace spans
- `span.name` — span label, such as a tool or LLM operation
- `span.inputs` / `span.outputs` — captured inputs and outputs for the span
- `span.span_type` or `span.type` — span category when exposed by the installed MLflow version

---

## Custom LLM Judge

For nuanced quality assessment, use the opt-in reasoning-quality scorer:

```python
from fleet_rlm.quality.scorers import reasoning_quality_scorer

judge = reasoning_quality_scorer("openai/gemini-3-flash-preview")
```

For the full recommended MLflow GenAI scorer set, call `build_rlm_scorers()`.

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
