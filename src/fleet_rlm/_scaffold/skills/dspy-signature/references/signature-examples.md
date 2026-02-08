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

From `src/fleet_rlm/signatures.py`:

```python
# Architecture extraction
"document, query -> modules, optimizers, design_principles"

# API endpoint extraction
"document -> endpoints, count"

# Error pattern analysis
"document -> error_patterns, categories, solutions"

# Custom tool extraction
"document, pattern -> matches, context"

# Long document analysis
"document, query -> findings, answer"

# Long document summarization
"document, focus -> summary, key_points"

# Log extraction
"logs, query -> matches, patterns"
```
