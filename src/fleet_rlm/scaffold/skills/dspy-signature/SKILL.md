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
from fleet_rlm.runtime.config import configure_planner_from_env
from fleet_rlm.integrations.providers.daytona.interpreter import DaytonaInterpreter

configure_planner_from_env()
signature = "question -> answer, confidence"

interpreter = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    timeout=120,
)
rlm = dspy.RLM(
    signature=signature,
    interpreter=interpreter,
    max_iterations=5,
    max_llm_calls=10,
)

try:
    result = rlm(question="What is the capital of France?")
    print(result.answer)       # Access via dot notation
    print(result.confidence)   # NOT result["confidence"]
finally:
    interpreter.shutdown()
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

Defined in `src/fleet_rlm/runtime/agent/signatures.py`:

| Signature                               | Fields                                                                                                                                    | Purpose                                         |
| --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `RLMReActChatSignature`                 | `user_request, core_memory, history -> assistant_response`                                                                                | Interactive ReAct chat with session history     |
| `SummarizeLongDocument`                 | `document, focus -> summary, key_points, coverage_pct`                                                                                    | Chunked multi-part document summarization       |
| `ExtractFromLogs`                       | `logs, query -> matches, patterns, time_range`                                                                                            | Log pattern extraction and categorization       |
| `GroundedAnswerWithCitations`           | `query, evidence_chunks, response_style -> answer, citations, confidence, coverage_notes`                                                 | Evidence-grounded answers with citation records |
| `IncidentTriageFromLogs`                | `logs, service_context, query -> severity, probable_root_causes, impacted_components, recommended_actions, time_range`                    | Incident diagnosis and triage                   |
| `CodeChangePlan`                        | `task, repo_context, constraints -> plan_steps, files_to_touch, validation_commands, risks`                                               | Structured code change planning                 |
| `CoreMemoryUpdateProposal`              | `turn_history, current_memory -> keep, update, remove, rationale`                                                                         | Safe core memory state updates                  |
| `VolumeFileTreeSignature`               | `root_path, max_depth, include_hidden -> nodes, total_files, total_dirs, truncated`                                                       | Bounded volume file-tree traversal              |
| `MemoryActionIntentSignature`           | `user_request, current_tree, policy_constraints -> action_type, target_paths, content_plan, risk_level, requires_confirmation, rationale` | Memory action intent classification             |
| `MemoryStructureAuditSignature`         | `tree_snapshot, usage_goals -> issues, recommended_layout, naming_conventions, retention_rules, priority_fixes`                           | Memory layout audit                             |
| `MemoryStructureMigrationPlanSignature` | `audit_findings, approved_constraints -> operations, rollback_steps, verification_checks, estimated_risk`                                 | Reversible memory migration planning            |
| `ClarificationQuestionSignature`        | `ambiguous_request, available_context, operation_risk -> questions, blocking_unknowns, safe_default, proceed_without_answer`              | Ambiguous operation clarification               |
| `RecursiveSubQuerySignature`            | `prompt, context -> answer`                                                                                                               | Bounded recursive sub-problem                   |
| `RLMVariableSignature`                  | `task, prompt -> answer`                                                                                                                  | Long-prompt variable exploration (Algorithm 1)  |
