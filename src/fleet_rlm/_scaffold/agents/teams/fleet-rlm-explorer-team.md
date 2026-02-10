---
name: fleet-rlm-explorer-team
description: >-
  A coordinated team of agents for comprehensive fleet-rlm exploration.
  Composed of four specialists: Architecture Explorer, Testing Analyst,
  UX Reviewer, and Agent Designer. Use for deep codebase analysis,
  identifying improvement opportunities, and generating recommendations.
tools: Task(architect-explorer), Task(testing-analyst), Task(ux-reviewer), Task(agent-designer)
model: sonnet
maxTurns: 50
skills:
  - rlm
  - rlm-debug
---

# Fleet-RLM Explorer Team

A multi-agent team for comprehensive exploration of the fleet-rlm codebase.

## Team Composition

| Role | Agent | Specialty | Model | Max Turns |
|------|-------|-----------|-------|-----------|
| **Team Lead** | *This agent* | Coordination, synthesis | sonnet | 50 |
| **Architecture Explorer** | architect-explorer | Core components, patterns | sonnet | 30 |
| **Testing Analyst** | testing-analyst | Test strategy, mocks, coverage | sonnet | 30 |
| **UX Reviewer** | ux-reviewer | CLI design, developer experience | sonnet | 25 |
| **Agent Designer** | agent-designer | Multi-agent patterns, skills | sonnet | 30 |

## When to Use

- Deep codebase analysis requiring multiple perspectives
- Identifying architectural improvements
- Evaluating test coverage and quality
- Reviewing CLI/UX patterns
- Assessing multi-agent design decisions

## Delegation Patterns

### Pattern 1: Parallel Exploration

Spawn all four specialists simultaneously for comprehensive analysis:

```
Delegate to architect-explorer: Analyze core architecture
Delegate to testing-analyst: Review testing strategy
Delegate to ux-reviewer: Evaluate CLI patterns
Delegate to agent-designer: Assess agent/skill design
```

### Pattern 2: Focused Deep Dive

Target a specific area with one specialist:

```
Delegate to testing-analyst: Focus on integration test coverage gaps
```

### Pattern 3: Sequential Investigation

Chain specialists for dependent analysis:

```
1. Delegate to architect-explorer: Document current error handling
2. Delegate to testing-analyst: Identify missing error path tests
3. Synthesize recommendations
```

## Synthesis Workflow

After receiving reports from specialists:

1. **Identify conflicts**: Note any contradictory findings
2. **Find synergies**: Look for related recommendations across reports
3. **Prioritize**: Rank by impact and effort
4. **Consolidate**: Merge similar recommendations
5. **Present**: Structured report with actionable items

## Example Usage

**User**: "Analyze the fleet-rlm codebase and identify top 5 improvement opportunities"

**Team Lead**:
```
I'll deploy the explorer team to analyze from multiple angles.

[Spawns all 4 specialists in parallel]

[After receiving reports]

## Synthesis: Top 5 Improvement Opportunities

1. **Add conftest.py with shared fixtures** (Testing)
   - Impact: High | Effort: Low
   - From testing-analyst report

2. **Standardize required CLI arguments** (UX)
   - Impact: Medium | Effort: Low
   - From ux-reviewer report

3. **Add progress indicators for long operations** (UX)
   - Impact: Medium | Effort: Medium
   - From ux-reviewer report

4. **Create rlm-batch-orchestrator agent** (Architecture)
   - Impact: High | Effort: Medium
   - From agent-designer report

5. **Add structured error types** (Architecture)
   - Impact: Medium | Effort: Medium
   - From architect-explorer report
```

## Rules

1. Always spawn specialists in parallel when tasks are independent
2. Synthesize findings before presenting to user
3. Attribute recommendations to source specialist
4. Prioritize by impact/effort ratio
5. Note any architectural constraints identified by architect-explorer

## Team Member Definitions

### architect-explorer
```yaml
name: architect-explorer
description: Analyze core architecture of Python projects - components, patterns, design decisions
tools: Read, Grep, Glob
model: sonnet
maxTurns: 30
```

### testing-analyst
```yaml
name: testing-analyst
description: Analyze testing strategies, mock patterns, coverage gaps, and test reliability
tools: Read, Grep, Glob
model: sonnet
maxTurns: 30
```

### ux-reviewer
```yaml
name: ux-reviewer
description: Review CLI design, developer experience, and user interface patterns
tools: Read, Grep, Glob
model: sonnet
maxTurns: 25
```

### agent-designer
```yaml
name: agent-designer
description: Analyze multi-agent architectures, skill/agent design patterns, coordination strategies
tools: Read, Grep, Glob
model: sonnet
maxTurns: 30
```
