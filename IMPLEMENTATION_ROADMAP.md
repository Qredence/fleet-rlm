# Fleet-RLM Implementation Roadmap

## Executive Summary

**Current State:** Phase 1-2 are **COMPLETED**. Core DSPy alignment achieved, file splitting done, Core Memory and Stateful Memory implemented.

**Remaining Work:** Phases 3-5, recursive sub-agents, and CodeAct integration.

---

## Gap Analysis Summary

| Category | Status | Count |
|----------|--------|-------|
| ✅ **Completed** | Fully implemented | 15 items |
|  **Phase 3** | Config & Observability | 3 items |
| 🔵 **Phase 4** | Interpreter & CodeAct | 3 items |
| 🟢 **Phase 5** | Polish & Optimization | 3 items |
| ⚪ **Future** | Recursive sub-agents | 1 item |

---

## Phase 1: Foundation (COMPLETED ✅)

All tasks completed and verified:

- [x] RLMReActChatAgent subclasses dspy.Module
- [x] forward() method implemented
- [x] All tools wrapped with dspy.Tool
- [x] Typed Signatures using generics
- [x] Core Memory (Tier 1) with core_memory_append/replace
- [x] Stateful Memory Tools (Tier 2) with memory_read/write/list
- [x] Documentation (concepts.md, user_flows.md, architecture.md)

---

## Phase 2: Sandbox Evolution (COMPLETED ✅)

All file splitting completed:

| Original File | New LOC | Split Files |
|--------------|---------|-------------|
| `tools.py` (933) | 626 | `tools.py` + `tools_sandbox.py` (360) |
| `runners.py` (798) | 258 | `runners.py` + `runners_demos.py` (574) |
| `cli.py` (976) | 595 | `cli.py` + `cli_demos.py` (416) |
| `test_react_agent.py` (935) | 400 | `test_react_agent.py` + `test_react_streaming.py` + `test_react_tools.py` |

---

## Phase 3: Config & Observability

### Task 3.1: Surface Trajectory + final_reasoning in Streaming
**Priority:** High
**Files:**
- `src/fleet_rlm/react/streaming.py`
- `src/fleet_rlm/react/agent.py`

**Implementation:**
1. Modify `iter_chat_turn_stream()` to capture `prediction.trajectory`
2. Add `final_reasoning` field to StreamEvent model
3. Emit trajectory events during streaming

**Code Location:** `src/fleet_rlm/react/streaming.py:47-90`

**Estimated Effort:** 2-3 hours

---

### Task 3.2: ~~Increase stdout_summary_threshold to 10,000~~ (COMPLETED ✅)
`stdout_summary_threshold` is already set to 10,000 in `src/fleet_rlm/core/interpreter.py`.

---

### Task 3.3: Add rlm_settings Section to config/config.yaml
**Priority:** High
**Files:**
- `config/config.yaml`
- `src/fleet_rlm/core/config.py`

**Implementation:**
1. Extend config.yaml schema with `rlm_settings` section:
```yaml
rlm_settings:
  max_iterations: 30
  max_llm_calls: 50
  max_output_chars: 10000
  stdout_summary_threshold: 10000
  verbose: false
```

2. Update `config.py` to load and validate rlm_settings

**Estimated Effort:** 2 hours

---

## Phase 4: Interpreter & CodeAct

### Task 4.1: Implement async execute() via Modal Async APIs
**Priority:** High
**File:** `src/fleet_rlm/core/interpreter.py`

**Implementation:**
1. Add `async def aexecute(self, code: str) -> str:` method
2. Use Modal's async sandbox APIs
3. Maintain backward compatibility with sync `execute()`

**Key Method Location:** `src/fleet_rlm/core/interpreter.py:450-520`

**Estimated Effort:** 4-6 hours

---

### Task 4.2: Fix ReAct Minor Gaps (Suggest/Assert)
**Priority:** Medium
**File:** `src/fleet_rlm/react/agent.py`

**Implementation:**
1. Add `dspy.Suggest` assertions for quality control
2. Add `dspy.Assert` for critical validations
3. Example:
```python
import dspy

# In forward() or tool execution:
dspy.Suggest(len(response) > 10, "Response seems too short")
dspy.Assert(tool_result is not None, "Tool must return a result")
```

**Estimated Effort:** 2 hours

---

### Task 4.3: Evaluate dspy.CodeAct with ModalInterpreter
**Priority:** High
**File:** `src/fleet_rlm/react/agent.py`

**Implementation:**
1. Add mode parameter to agent initialization:
```python
def __init__(self, *, mode="react", ...):
    if mode == "codeact":
        self.agent = dspy.CodeAct(
            signature=RLMReActChatSignature,
            tools=self.react_tools,
            interpreter=self.interpreter,
            max_iters=self.react_max_iters,
        )
    else:
        self.agent = dspy.ReAct(...)
```

2. Ensure ModalInterpreter is compatible with CodeAct's interpreter requirements
3. Add tests for CodeAct mode

**Note:** Per user directive, CodeAct must use ModalInterpreter (cloud sandbox), NOT Deno/Pyodide.

**Estimated Effort:** 6-8 hours

---

## Phase 5: Polish & Optimization

### Task 5.1: Pool batch ThreadPoolExecutor at Interpreter Level
**Priority:** Low
**File:** `src/fleet_rlm/core/interpreter.py`

**Implementation:**
1. Create shared ThreadPoolExecutor at interpreter initialization
2. Reuse executor across multiple RLM calls
3. Proper shutdown on interpreter cleanup

**Estimated Effort:** 3 hours

---

### Task 5.2: Bundle driver.py into Modal Image at Build Time
**Priority:** Low
**Files:**
- `src/fleet_rlm/core/interpreter.py`
- Modal image definition

**Implementation:**
1. Modify Modal image build to include driver.py
2. Update interpreter to use bundled driver instead of runtime upload
3. Improve startup performance

**Estimated Effort:** 4 hours

---

### Task 5.3: with_instructions() for Dynamic Prompts
**Priority:** Low
**File:** `src/fleet_rlm/signatures.py`

**Implementation:**
1. Update signatures to support runtime instruction injection:
```python
signature = ExtractArchitecture.with_instructions(
    "Focus on extracting async patterns"
)
```

**Estimated Effort:** 2 hours

---

## Phase 6: Recursive Sub-Agents (Future)

### Task 6.1: Implement Recursive Sub-Agent Spawning via dspy.RLM
**Priority:** Future
**File:** `src/fleet_rlm/react/tools_sandbox.py`

**Implementation:**
1. Add depth tracking to RLM calls
2. Implement parent-child RLM relationship
3. Add sub-agent spawning tool:
```python
def spawn_sub_agent(task: str, depth: int = 0) -> dict:
    """Spawn a child RLM for sub-task delegation."""
    if depth >= MAX_RECURSION_DEPTH:
        return {"error": "Max recursion depth reached"}

    child_rlm = dspy.RLM(
        signature="task -> result",
        interpreter=agent.interpreter,
        max_iterations=agent.rlm_max_iterations // 2,
    )
    result = child_rlm(task=task)
    return {"result": result, "depth": depth}
```

**Estimated Effort:** 8-12 hours

---

## Task Dependencies

```
Phase 0 (Bug Fix)
    │
    ▼
Phase 3 (Config)
    ├── Task 3.1 ────┐
    ├── Task 3.2 ────┼──► Phase 4 (CodeAct)
    └── Task 3.3 ────┘         │
                               ├── Task 4.1 (async)
                               ├── Task 4.2 (Suggest/Assert)
                               └── Task 4.3 (CodeAct)
                                    │
                                    ▼
                               Phase 5 (Polish)
                                    │
                                    ▼
                               Phase 6 (Recursive)
```

---

## Success Metrics

| Phase | Completion Criteria |
|-------|-------------------|
| Phase 0 | `pytest tests/unit/test_react_agent.py -x` passes |
| Phase 3 | All config values loadable from YAML |
| Phase 4 | `mode="codeact"` agent passes integration tests |
| Phase 5 | 10% faster interpreter startup |
| Phase 6 | Recursive depth tracking verified |

---

## Test Coverage Requirements

Each task must include:
1. Unit tests for new functionality
2. Integration tests where applicable
3. Import verification: `python -c "from fleet_rlm import X"`
4. Ruff compliance: `ruff check src tests`

---

*Generated: 2026-02-14*
*Based on: plans/ directory analysis and current codebase state*
