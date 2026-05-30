---
name: dspy-programs
description: "Design DSPy signatures and compose runtime modules for fleet-rlm tasks. Use when creating input/output field definitions, choosing between built-in signatures, selecting execution modes, or wiring custom modules."
---

# DSPy Programs — Signatures and Module Composition

Design DSPy signatures, select execution modes, and compose runtime modules
for fleet-rlm tasks. Covers the signature syntax contract, the built-in
signature catalog, execution mode routing, and the module registry.

---

## Signature Syntax

DSPy signatures define the input/output contract for a program.

**Format**: `"input1, input2 -> output1, output2"`

**Rules**:
- Must contain the `->` separator (exactly one).
- Field names use `snake_case`.
- Names should be descriptive (prefer `error_messages` over `msgs`).
- Use plural names for list-valued fields (`key_points`, not `key_point`).
- No Python reserved words (`class`, `import`, `return`, etc.).
- No dots in field names.

**Quick Examples**:

```python
"question -> answer"
"context, question -> answer, confidence"
"document -> summary, key_points, coverage_pct"
"logs, service_context, query -> severity, probable_root_causes, recommended_actions"
```

---

## Built-in Signatures

From `src/fleet_rlm/runtime/agent/signatures.py`:

| Signature Class | Fields | Purpose |
| --- | --- | --- |
| `RLMReActChatSignature` | `user_request, core_memory, history -> assistant_response` | Lightweight ReAct chat with conversation history |
| `RLMVariableSignature` | `task, prompt -> answer` | Heavy-path variable-mode execution (Algorithm 1) |
| `RecursiveSubQuerySignature` | `prompt, context -> answer` | Child delegation — bounded recursive sub-query |
| `SummarizeLongDocument` | `document, focus -> summary, key_points, coverage_pct` | Long document summarization with focus control |
| `ExtractFromLogs` | `logs, query -> matches, patterns, time_range` | Log pattern extraction and time-range identification |
| `GroundedAnswerWithCitations` | `query, evidence_chunks, response_style -> answer, citations, confidence, coverage_notes` | Evidence-grounded answer with source citations |
| `IncidentTriageFromLogs` | `logs, service_context, query -> severity, probable_root_causes, impacted_components, recommended_actions, time_range` | Incident triage from service logs |
| `CodeChangePlan` | `task, repo_context, constraints -> plan_steps, files_to_touch, validation_commands, risks` | Structured code change planning with risk assessment |
| `CoreMemoryUpdateProposal` | `turn_history, current_memory -> keep, update, remove, rationale` | Propose updates to persistent core memory |

---

## Execution Mode Decision

The runtime selects an execution mode based on the request characteristics:

| Mode | Module | When Selected |
| --- | --- | --- |
| Default | `EscalatingFleetModule` | CoT first; escalates to RLM if `[TOOLS NEEDED]` detected in output |
| Variable-mode | `RLMVariableExecutionModule` | Input exceeds 32K chars — LLM sees metadata, explores via code |
| Grounded-answer | `GroundedAnswerWithCitations` | Evidence-based queries requiring citations |
| Recursive workspace | `RecursiveWorkspaceModule` | Multi-step workspace operations with tool use |
| Force RLM | (direct `dspy.RLM`) | Caller sets `execution_mode="rlm"` in request to skip lightweight path |

**Decision flow**:
1. If `execution_mode="rlm"` is set explicitly, go directly to `dspy.RLM`.
2. If input length > 32K characters, route to `RLMVariableExecutionModule`.
3. If evidence chunks are provided with a query, route to `GroundedAnswerWithCitations`.
4. Otherwise, start with `EscalatingFleetModule` (lightweight CoT).
5. If the CoT output contains `[TOOLS NEEDED]`, escalate to full RLM execution.

---

## Programmatic RLM Instantiation

```python
import dspy
from fleet_rlm.runtime.config import configure_planner_from_env
from fleet_rlm.integrations.daytona.interpreter import DaytonaInterpreter
from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument

configure_planner_from_env()

interp = DaytonaInterpreter(
    repo_url="https://github.com/your-org/your-repo",
    volume_name="rlm-volume-dspy",
    timeout=900,
)
interp.start()
try:
    rlm = dspy.RLM(
        signature=SummarizeLongDocument,
        interpreter=interp,
        max_iterations=20,
        max_llm_calls=30,
        verbose=True,
    )
    result = rlm(document=text, focus="design decisions")
    print(result.summary)
    print(result.key_points)
    print(result.coverage_pct)
finally:
    interp.shutdown()
```

**Key parameters**:
- `signature`: The DSPy signature class (not a string).
- `interpreter`: A started `DaytonaInterpreter` instance.
- `max_iterations`: Maximum code-execution loops (default: 20).
- `max_llm_calls`: Budget cap for LLM invocations (default: 30).
- `verbose`: Emit step-by-step execution logs.

---

## Registering Custom Modules

To add a new module to the fleet-rlm runtime:

1. **Define the signature** in `runtime/agent/signatures.py`:

```python
class MyCustomSignature(dspy.Signature):
    """One-line description of what this signature does."""
    input_field: str = dspy.InputField(desc="Description of input")
    output_field: str = dspy.OutputField(desc="Description of output")
```

2. **Implement the module** (must implement `dspy.Module` interface):

```python
class MyCustomModule(dspy.Module):
    def __init__(self):
        super().__init__()
        self.predict = dspy.Predict(MyCustomSignature)

    def forward(self, **kwargs):
        return self.predict(**kwargs)
```

3. **Register in `quality/module_registry.py`**:

```python
from fleet_rlm.quality.module_registry import register_module

register_module(
    name="my_custom_task",
    signature=MyCustomSignature,
    module_cls=MyCustomModule,
    default_params={"max_iterations": 15, "max_llm_calls": 20},
    eval_metric="exact_match",  # or custom metric function
)
```

4. **Add an evaluation metric** (optional but recommended):

```python
def my_metric(example, prediction, trace=None):
    return prediction.output_field == example.expected_output
```

---

## See Also

- **sandbox-execution** — Interpreter lifecycle (`DaytonaInterpreter` start/shutdown)
- **delegation** — Child signature selection for delegated sub-tasks
- **optimization** — Tuning registered modules with DSPy optimizers
- **long-context** — Signatures used in document decomposition workflows
