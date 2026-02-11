# RLM Output Validation Report Template

## Overview
This template defines the validation criteria for RLM outputs based on DSPy signatures.

---

## Validation Criteria by Signature

### 1. ExtractArchitecture

**Required Output Fields:**
- [ ] `modules`: List of DSPy modules identified
- [ ] `optimizers`: List of available optimizers
- [ ] `design_principles`: Key design principles (string)

**Validation Checks:**
- [ ] `modules` is a non-empty list
- [ ] `optimizers` is a non-empty list
- [ ] `design_principles` is a non-empty string
- [ ] All modules mentioned exist in source documentation
- [ ] All optimizers mentioned exist in source documentation
- [ ] Design principles accurately reflect documentation

**Sample Valid Output:**
```python
{
    "modules": ["ChainOfThought", "ReAct", "Predict", "ProgramOfThought"],
    "optimizers": ["BootstrapFewShot", "COPRO", "MIPRO"],
    "design_principles": "DSPy emphasizes declarative programming..."
}
```

---

### 2. ExtractAPIEndpoints

**Required Output Fields:**
- [ ] `api_endpoints`: List of API endpoints with details

**Validation Checks:**
- [ ] `api_endpoints` is a list
- [ ] Each endpoint has required fields (path, method, description)
- [ ] Endpoints are from the actual API documentation
- [ ] No fabricated endpoints

---

### 3. FindErrorPatterns

**Required Output Fields:**
- [ ] `error_categories`: Dict mapping error types to solutions
- [ ] `total_errors_found`: Integer count

**Validation Checks:**
- [ ] `error_categories` is a dictionary
- [ ] `total_errors_found` is an integer >= 0
- [ ] Error categories match actual errors in documentation
- [ ] Solutions are accurate and complete

---

### 4. AnalyzeLongDocument

**Required Output Fields:**
- [ ] `findings`: List of extracted facts/answers
- [ ] `answer`: Synthesised prose answer (string)
- [ ] `sections_examined`: Integer count

**Validation Checks:**
- [ ] `findings` is a non-empty list
- [ ] `answer` is a non-empty string
- [ ] `sections_examined` is an integer >= 1
- [ ] Findings directly address the query
- [ ] Answer synthesizes findings coherently
- [ ] Sections examined is reasonable for document size

---

### 5. SummarizeLongDocument

**Required Output Fields:**
- [ ] `summary`: Coherent summary text
- [ ] `key_points`: Bullet-point list
- [ ] `coverage_pct`: Integer (0-100)

**Validation Checks:**
- [ ] `summary` is a non-empty string
- [ ] `key_points` is a non-empty list
- [ ] `coverage_pct` is an integer between 0-100
- [ ] Summary focuses on requested topic
- [ ] Key points are distinct and relevant
- [ ] Coverage percentage is realistic

---

## Common Validation Issues

### Completeness Issues
- Missing required output fields
- Empty lists or strings when content should exist
- Partial extraction (missing key items)

### Accuracy Issues
- Hallucinated content not in source
- Misattributed information
- Incorrect relationships between items

### Format Issues
- Wrong data types (e.g., string instead of list)
- Inconsistent naming conventions
- Malformed structures

### Quality Issues
- Vague or generic responses
- Redundant or duplicate information
- Poor synthesis of findings

---

## Validation Workflow

1. **Load Source Document**: Read the original document that was processed
2. **Load RLM Output**: Get the structured output from RLM execution
3. **Check Field Presence**: Verify all required fields exist
4. **Validate Types**: Ensure each field has correct Python type
5. **Verify Content**: Cross-reference with source for accuracy
6. **Assess Quality**: Evaluate completeness and usefulness
7. **Document Issues**: Record any discrepancies found

---

## Validation Report Format

```markdown
## Validation Report: [Task Name]

### Source Document
- File: [path/to/document]
- Size: [X lines / Y tokens]

### RLM Output
- Signature Used: [signature name]
- Execution Time: [X seconds]

### Field Validation
| Field | Status | Notes |
|-------|--------|-------|
| field1 | ✅/⚠️/❌ | [notes] |

### Content Accuracy
- [ ] All items verified against source
- Issues found: [list]

### Overall Assessment
- Status: [PASS / NEEDS_REVIEW / FAIL]
- Confidence: [HIGH / MEDIUM / LOW]
```
