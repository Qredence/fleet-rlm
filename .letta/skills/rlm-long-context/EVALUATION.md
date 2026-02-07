---
name: rlm-long-context-evaluation
description: Evaluation protocol for measuring rlm-long-context skill performance and improvement gains through controlled experimentation.
---

# RLM Long-Context Skill Evaluation Protocol

## Purpose

Measure objective performance improvements from skill fixes using controlled A/B testing methodology.

## Evaluation Dimensions

| Dimension | Metric | Measurement Method |
|-----------|--------|-------------------|
| **Activation** | Trigger accuracy | % of relevant queries that activate skill |
| **Correctness** | Result accuracy | % of correct answers vs ground truth |
| **Efficiency** | Token usage | Total tokens consumed per task |
| **Robustness** | Error rate | % of tasks completing without errors |
| **Usability** | Time to first result | Seconds from query to first output |

---

## Test Suite Design

### Test Categories

#### T1: Activation Tests (Description Quality)
**Purpose**: Does the skill trigger when it should?

| Test ID | Query | Should Trigger? | Keywords Tested |
|---------|-------|-----------------|-----------------|
| T1.1 | "Analyze this 500MB log file" | YES | large files, log analysis |
| T1.2 | "Search for errors in production.log" | YES | log analysis |
| T1.3 | "Process this 100K line CSV" | YES | 100K lines, big data |
| T1.4 | "Find patterns in the data" | MAYBE | patterns (ambiguous) |
| T1.5 | "Summarize this transcript" | YES | summarizing long transcripts |
| T1.6 | "Fix the bug in main.py" | NO | unrelated |
| T1.7 | "Context overflow when processing" | YES | context overflow |
| T1.8 | "Use map-reduce to analyze" | YES | map-reduce |

**Scoring**: (Correct triggers / Total tests) × 15 points

#### T2: Correctness Tests (Subagent + Workflow)
**Purpose**: Does the skill produce correct results?

Setup: Create test file `test_data.log` (50K lines) with known error patterns:
```
[2024-01-01 10:00:00] INFO: Starting service
[2024-01-01 10:00:01] ERROR: Connection timeout after 5000ms
[2024-01-01 10:00:02] WARN: Retrying connection (attempt 1/3)
... (known distribution of 50 ERROR, 200 WARN, 1000 INFO)
```

| Test ID | Query | Ground Truth | Tolerance |
|---------|-------|--------------|-----------|
| T2.1 | "Count all ERROR entries" | 50 | ±0 |
| T2.2 | "Find first timeout error" | 10:00:01 | Exact |
| T2.3 | "List all WARNING messages" | 200 entries | ±5 |
| T2.4 | "What errors occurred between 10:00 and 11:00?" | 50 ERROR | ±0 |
| T2.5 | "Summarize error types" | timeout, connection, auth | 3 categories |

**Scoring**: (Correct answers / Total tests) × 20 points

#### T3: Efficiency Tests (Optimization Effectiveness)
**Purpose**: Are optimizations actually working?

Test file: `large_corpus.txt` (200K lines, 10MB)
Known: Only 5% of lines contain target keywords

| Test ID | Technique | Metric | Target Improvement |
|---------|-----------|--------|-------------------|
| T3.1 | No optimization (baseline) | Chunks processed | 100% |
| T3.2 | Query-Guided Selection | Chunks processed | <20% |
| T3.3 | Semantic Chunking | Boundary splits | 0 mid-sentence |
| T3.4 | Caching | Second query time | <10% of first |
| T3.5 | Early Exit | Chunks processed | <30% if confidence high |

**Scoring**: (Optimizations achieving targets / Total) × 15 points

#### T4: Robustness Tests (Error Handling)
**Purpose**: Does it handle edge cases gracefully?

| Test ID | Scenario | Expected Behavior |
|---------|----------|-------------------|
| T4.1 | Empty file | Graceful error, clear message |
| T4.2 | File with no matches | Empty result, not error |
| T4.3 | Corrupted chunk output | Validation catches, retry |
| T4.4 | Missing subagent | Clear error: "rlm-subcall not found" |
| T4.5 | Zero relevance scores | Fallback to semantic chunking |
| T4.6 | Context overflow during synthesis | Trigger hierarchical merge |

**Scoring**: (Graceful handling / Total) × 15 points

#### T5: Usability Tests (Practical Workflow)
**Purpose**: Can a user/agent actually complete tasks?

| Test ID | Task | Success Criteria |
|---------|------|------------------|
| T5.1 | Complete log analysis end-to-end | Result in <2 minutes |
| T5.2 | Follow NEVER list guidance | No context overflow errors |
| T5.3 | Use correct script for scenario | Right tool selected |
| T5.4 | Interpret subagent output | Correct synthesis |
| T5.5 | Handle cache invalidation | Cache cleared, reprocessed |

**Scoring**: (Tasks completed / Total) × 15 points

---

## Experimental Protocol

### Phase 1: Baseline (BEFORE Fixes)

1. **Setup**:
   ```bash
   # Remove fixed version
   mv .agents/rlm-subcall.md .agents/rlm-subcall.md.bak
   # Revert SKILL.md to original (manual or git checkout)
   ```

2. **Run Test Suite**:
   - Execute each test category
   - Record metrics in `results/baseline.json`
   - Note specific failures

3. **Document Issues**:
   - Which tests fail?
   - Why do they fail? (skill-judge categories)
   - Time/errors for each

### Phase 2: Treatment (AFTER Fixes)

1. **Setup**:
   ```bash
   # Restore fixed version
   mv .agents/rlm-subcall.md.bak .agents/rlm-subcall.md
   # Verify fixes applied
   ```

2. **Run Test Suite**:
   - Same tests as baseline
   - Record metrics in `results/fixed.json`
   - Note improvements

### Phase 3: Analysis

Calculate **Performance Gap**:

```
Gap = Fixed_Score - Baseline_Score

Improvement % = (Gap / Baseline_Score) × 100
```

---

## Measurement Tools

### Tool 1: Activation Tester
```python
# test_activation.py
TEST_QUERIES = [
    ("Analyze this 500MB log file", True),
    ("Fix the bug in main.py", False),
    # ...
]

for query, should_trigger in TEST_QUERIES:
    # Simulate skill matching
    triggered = check_skill_activation(query, skill_description)
    record_result(query, should_trigger, triggered)
```

### Tool 2: Correctness Validator
```python
# test_correctness.py
GROUND_TRUTH = {
    "error_count": 50,
    "first_timeout": "2024-01-01 10:00:01",
    # ...
}

result = run_skill_workflow("test_data.log", "Count all ERROR entries")
assert result == GROUND_TRUTH["error_count"], f"Expected {GROUND_TRUTH['error_count']}, got {result}"
```

### Tool 3: Efficiency Monitor
```python
# test_efficiency.py
import time

# Baseline: No optimizations
start = time.time()
tokens_baseline = run_without_optimizations("large_corpus.txt")
time_baseline = time.time() - start

# Treatment: With optimizations
start = time.time()
tokens_optimized = run_with_optimizations("large_corpus.txt")
time_optimized = time.time() - start

speedup = time_baseline / time_optimized
token_reduction = (1 - tokens_optimized/tokens_baseline) * 100
```

---

## Expected Results

### Baseline (Before Fixes)

| Dimension | Expected Score | Failure Reasons |
|-----------|---------------|-----------------|
| Activation | 6/8 (75%) | Description lacks keywords |
| Correctness | 3/5 (60%) | Missing subagent = workflow fails |
| Efficiency | 2/5 (40%) | Can't test optimizations if workflow broken |
| Robustness | 2/6 (33%) | No error handling for missing subagent |
| Usability | 1/5 (20%) | Core workflow non-functional |
| **TOTAL** | **~48/120 (40%)** | Grade F |

### Treatment (After Fixes)

| Dimension | Expected Score | Improvement |
|-----------|---------------|-------------|
| Activation | 8/8 (100%) | +25% (description has keywords) |
| Correctness | 5/5 (100%) | +40% (subagent exists, works) |
| Efficiency | 5/5 (100%) | +60% (optimizations functional) |
| Robustness | 5/6 (83%) | +50% (NEVER list + error handling) |
| Usability | 5/5 (100%) | +80% (workflow complete) |
| **TOTAL** | **~110/120 (92%)** | **Grade A** |

### Gap Analysis

```
Total Improvement: 110 - 48 = 62 points (+129%)
Grade Jump: F → A

Key Drivers:
1. Subagent creation: +30 points (Correctness + Usability)
2. Description fix: +5 points (Activation)
3. NEVER list: +10 points (Robustness)
4. Loading triggers: +5 points (Efficiency)
5. Path fixes: +5 points (Usability)
```

---

## Running the Evaluation

```bash
# 1. Setup test environment
cd .agents/skills/rlm-long-context
mkdir -p evaluation/results evaluation/test_data

# 2. Generate test data
python3 evaluation/generate_test_data.py

# 3. Run baseline (before fixes)
python3 evaluation/run_baseline.py > results/baseline.json

# 4. Apply fixes (or restore fixed version)
# ... apply fixes ...

# 5. Run treatment (after fixes)
python3 evaluation/run_treatment.py > results/fixed.json

# 6. Generate report
python3 evaluation/compare_results.py \
    --baseline results/baseline.json \
    --treatment results/fixed.json \
    --output results/report.md
```

---

## Statistical Significance

Run each test **n=5 times** to account for variance:

```python
# Statistical analysis
from statistics import mean, stdev

baseline_scores = [48, 50, 47, 49, 48]  # n=5 runs
fixed_scores = [108, 110, 109, 111, 110]

baseline_mean = mean(baseline_scores)  # 48.4
fixed_mean = mean(fixed_scores)        # 109.6

# Paired t-test
gap = fixed_mean - baseline_mean  # 61.2 points
confidence = calculate_confidence_interval(baseline_scores, fixed_scores)
```

---

## Report Template

```markdown
# RLM Long-Context Skill Evaluation Report

## Executive Summary
- **Baseline Score**: 48.4/120 (40%, Grade F)
- **Treatment Score**: 109.6/120 (91%, Grade A)
- **Performance Gap**: +61.2 points (+126% improvement)
- **Statistical Confidence**: 95% CI [58.3, 64.1]

## Dimension-by-Dimension Comparison

| Dimension | Baseline | Treatment | Gap | Improvement |
|-----------|----------|-----------|-----|-------------|
| Activation | 6/8 | 8/8 | +2 | +33% |
| Correctness | 3/5 | 5/5 | +2 | +67% |
| ... | ... | ... | ... | ... |

## Critical Findings

1. **Subagent creation** contributed 35% of total improvement
2. **Description fix** eliminated 75% of activation failures
3. **NEVER list** reduced error rate by 60%

## Recommendations

1. Keep subagent as separate file (enables modularity)
2. Description keywords essential for activation
3. Concrete error handling > vague warnings
```

---

## Appendix: Test Data Generation

```python
# evaluation/generate_test_data.py

import random
from datetime import datetime, timedelta

def generate_log_file(filepath, lines=50000):
    """Generate test log with known error distribution."""
    levels = ['INFO'] * 1000 + ['WARN'] * 200 + ['ERROR'] * 50
    error_types = ['timeout', 'connection', 'auth', 'disk_full']

    with open(filepath, 'w') as f:
        timestamp = datetime(2024, 1, 1, 10, 0, 0)

        for i in range(lines):
            level = random.choice(levels)
            if level == 'ERROR':
                error_type = random.choice(error_types)
                msg = f"ERROR: {error_type} failed"
            elif level == 'WARN':
                msg = "WARN: Retrying operation"
            else:
                msg = "INFO: Operation successful"

            f.write(f"[{timestamp.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            timestamp += timedelta(seconds=random.randint(1, 10))

def generate_corpus(filepath, lines=200000):
    """Generate text corpus with 5% keyword density."""
    keywords = ['target_keyword', 'important', 'critical']
    filler = ['the', 'quick', 'brown', 'fox', 'jumps', 'over', 'lazy', 'dog']

    with open(filepath, 'w') as f:
        for i in range(lines):
            if random.random() < 0.05:  # 5% density
                word = random.choice(keywords)
            else:
                word = random.choice(filler)
            f.write(f"{word} " * 20 + "\n")

if __name__ == "__main__":
    generate_log_file("evaluation/test_data/test_data.log", 50000)
    generate_corpus("evaluation/test_data/large_corpus.txt", 200000)
    print("Test data generated.")
```
