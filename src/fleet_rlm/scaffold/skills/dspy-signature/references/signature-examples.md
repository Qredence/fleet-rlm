# DSPy Signature Examples by Category

## Question Answering

```python
"question -> answer"
"context, question -> answer"
"context, question -> answer, confidence"
"document, question -> answer, citations, confidence"
"documents, question -> answer, reasoning_steps"
```

## Summarization

```python
"text -> summary"
"document -> summary, key_points, word_count"
"doc1, doc2 -> comparison, similarities, differences"
"report -> executive_summary, recommendations, action_items"
```

## Code Tasks

```python
"description -> code, language"
"code -> explanation, complexity_analysis"
"code -> review_comments, issues, suggestions"
"code -> bugs, severity_scores, fixes"
"code, requirements -> refactored_code, changes_made"
"code -> test_cases, coverage_analysis"
"code -> docstring, usage_examples"
```

## Information Extraction

```python
"text -> entities, entity_types, confidence_scores"
"text -> relations, subject, object, relation_type"
"document -> extracted_fields, missing_fields"
"document -> tables, headers, rows"
"text -> names, emails, phones, addresses"
```

## Classification

```python
"text -> sentiment, confidence"
"text -> categories, confidences"
"query -> intent, confidence, entities"
"task_description -> priority, urgency, effort"
```

## RLM-Specific

```python
"task_description -> python_code, expected_output"
"query -> tool_name, tool_args, reasoning"
"complex_task -> subtasks, delegation_plan"
"code, error -> fixed_code, explanation"
"dataset, query -> findings, statistics, insights"
```

## fleet-rlm Built-in Signatures

From `src/fleet_rlm/runtime/agent/signatures.py`:

```python
# Interactive ReAct chat with conversation history
"user_request, core_memory, history -> assistant_response"

# Long document summarization with focus control
"document, focus -> summary, key_points, coverage_pct"

# Log pattern extraction
"logs, query -> matches, patterns, time_range"

# Evidence-grounded answer with citations
"query, evidence_chunks, response_style -> answer, citations, confidence, coverage_notes"

# Incident triage from logs
"logs, service_context, query -> severity, probable_root_causes, impacted_components, recommended_actions, time_range"

# Structured code change planning
"task, repo_context, constraints -> plan_steps, files_to_touch, validation_commands, risks"

# Core memory update proposal
"turn_history, current_memory -> keep, update, remove, rationale"

# Volume file-tree traversal
"root_path, max_depth, include_hidden -> nodes, total_files, total_dirs, truncated"

# Memory action intent classification
"user_request, current_tree, policy_constraints -> action_type, target_paths, content_plan, risk_level, requires_confirmation, rationale"

# Recursive sub-query (bounded delegation)
"prompt, context -> answer"

# Long-prompt variable exploration (Algorithm 1)
"task, prompt -> answer"
```
