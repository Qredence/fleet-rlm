---
name: dspy-signature
description: Generate and validate DSPy signatures for RLM tasks. Use when creating input/output field definitions for dspy.RLM, choosing field names, or designing task signatures.
---

# DSPy Signature Generator

Signatures define input/output structure for dspy.RLM tasks. Format: `"input1, input2 -> output1, output2"`.

## Signature Syntax

```
input_field1, input_field2 -> output_field1, output_field2
```

**Rules:**

- Use `snake_case` for field names
- Be descriptive: `source_code` not `sc`
- Use plural for lists: `items`, `results`
- Avoid Python reserved words: `input`, `output`, `type`
- No duplicate field names across inputs and outputs
- Must contain `->` separator

## Quick Examples

```python
# Simple QA
"question -> answer"

# Context-based QA with confidence
"context, question -> answer, confidence"

# Code analysis
"code -> explanation, complexity_analysis"

# Document summarization
"document -> summary, key_points, word_count"

# RLM exploration
"dataset, query -> findings, statistics, insights"
```

For extensive examples by category, see [references/signature-examples.md](references/signature-examples.md).

## Usage with fleet-rlm

```python
import dspy
from fleet_rlm import ModalInterpreter

signature = "question -> answer, confidence"

interpreter = ModalInterpreter(timeout=120)
rlm = dspy.RLM(
    signature=signature,
    interpreter=interpreter,
    max_iterations=5,
    max_llm_calls=10,
)

result = rlm(question="What is the capital of France?")
print(result.answer)       # Access via dot notation
print(result.confidence)   # NOT result["confidence"]
```

## Common Field Types

| Field                | Description               | Example Values |
| -------------------- | ------------------------- | -------------- |
| `text` / `document`  | Raw or long-form content  | String         |
| `question` / `query` | Query to answer or search | String         |
| `context`            | Background information    | String         |
| `code`               | Source code               | String         |
| `answer` / `result`  | Single outcome            | String         |
| `results` / `items`  | Multiple outcomes         | List           |
| `summary`            | Condensed text            | String         |
| `count`              | Numeric count             | Integer        |
| `confidence`         | 0-1 score                 | Float          |

## Best Practices

**DO:**

- Be specific: `python_code` not `code` when context is clear
- Include metadata fields: `confidence`, `explanation` when useful
- Consider downstream consumers of the output
- Use consistent naming: same field name = same semantics

**DON'T:**

- Use single letters (`x`, `q`)
- Overload fields (one concept per field)
- Use dots in names (`user.name` -> `user_name`)

## Advanced Patterns

### Conditional Outputs

```python
"code -> result, error, success"
# success: bool, result: output if True, error: message if False
```

### Iteration Tracking

```python
"task -> result, steps_taken, iterations_used"
```

## fleet-rlm Built-in Signatures

Defined in `src/fleet_rlm/signatures.py`:

- `ExtractArchitecture`: `document, query -> modules, optimizers, design_principles`
- `ExtractAPIEndpoints`: `document -> endpoints, count`
- `FindErrorPatterns`: `document -> error_patterns, categories, solutions`
- `ExtractWithCustomTool`: `document, pattern -> matches, context`
- `AnalyzeLongDocument`: `document, query -> findings, answer`
- `SummarizeLongDocument`: `document, focus -> summary, key_points`
- `ExtractFromLogs`: `logs, query -> matches, patterns`
