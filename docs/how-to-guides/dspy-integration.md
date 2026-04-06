# DSPy Integration Guide

This guide explains how to use DSPy primitives (Signatures, Modules, RLM) within fleet-rlm. DSPy provides a declarative framework for building LLM-powered applications with structured inputs/outputs and optimization support.

## Overview

Fleet-rlm extends DSPy with `dspy.RLM`, a recursive language model runtime that executes LLM calls inside a Daytona sandbox. This enables:

- **Long-context reasoning**: The sandbox can load and process documents that exceed typical context limits
- **Code execution**: Run Python code to explore data, manipulate files, and call APIs
- **Tool orchestration**: Use sandbox helpers (`peek`, `grep`, `chunk_by_size`) alongside LLM reasoning
- **MLflow tracing**: Capture execution traces for debugging and optimization

## Signatures

Signatures define the input/output contract for DSPy modules. Fleet-rlm keeps all production signatures centralized in `src/fleet_rlm/runtime/agent/signatures.py`.

### Signature Structure

Every signature follows the DSPy pattern:

```python
import dspy

class MySignature(dspy.Signature):
    """Docstring describing what the signature does.

    Input Fields:
        input1: Description of first input
        input2: Description of second input

    Output Fields:
        output1: Description of first output
        output2: Description of second output
    """

    input1: str = dspy.InputField(desc="First input description")
    input2: str = dspy.InputField(desc="Second input description")
    output1: str = dspy.OutputField(desc="First output description")
    output2: list[str] = dspy.OutputField(desc="Second output description")
```

### Built-in Signatures

Fleet-rlm provides several production-ready signatures:

#### Long-Document Summarization

```python
from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument

class SummarizeLongDocument(dspy.Signature):
    """Summarize a long document with controllable focus.

    The LLM should use sandbox helpers (peek, grep, chunk_by_size,
    chunk_by_headers) to explore the document programmatically, call
    llm_query on relevant sections, and aggregate findings via SUBMIT.
    """
    document: str = dspy.InputField(desc="Full document text (loaded in sandbox)")
    focus: str = dspy.InputField(desc="Summarization focus or topic")
    key_points: list[str] = dspy.OutputField(desc="List of key points extracted")
    summary: str = dspy.OutputField(desc="Synthesised prose summary")
```

```python
from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument

class SummarizeLongDocument(dspy.Signature):
    """Summarize a long document with controllable focus.

    The LLM should chunk the document, query each chunk with the given
    focus topic, and merge the per-chunk summaries into a coherent whole.
    """
    document: str = dspy.InputField(desc="Full document text")
    focus: str = dspy.InputField(desc="Topic or aspect to focus on")
    summary: str = dspy.OutputField(desc="Coherent summary text")
    key_points: list[str] = dspy.OutputField(desc="Bullet-point list of key takeaways")
    coverage_pct: int = dspy.OutputField(desc="Estimated percentage of document covered (0-100)")
```

#### Log Analysis

```python
from fleet_rlm.runtime.agent.signatures import ExtractFromLogs, IncidentTriageFromLogs

class ExtractFromLogs(dspy.Signature):
    """Extract patterns from log-style text."""
    logs: str = dspy.InputField(desc="Full log text")
    query: str = dspy.InputField(desc="Pattern or topic to search for")
    matches: list[str] = dspy.OutputField(desc="List of matching log entries")
    patterns: dict[str, str] = dspy.OutputField(desc="Dict mapping category to example entries")
    time_range: str = dspy.OutputField(desc="Observed time range of matching entries")

class IncidentTriageFromLogs(dspy.Signature):
    """Triage incident logs into operator-ready diagnostics."""
    logs: str = dspy.InputField(desc="Raw log text to analyze")
    service_context: str = dspy.InputField(desc="Service/environment context to guide triage")
    query: str = dspy.InputField(desc="Primary investigation question")
    severity: str = dspy.OutputField(desc="Incident severity: low, medium, high, or critical")
    probable_root_causes: list[str] = dspy.OutputField(desc="Likely root causes inferred from log evidence")
    impacted_components: list[str] = dspy.OutputField(desc="Components likely affected by the incident")
    recommended_actions: list[str] = dspy.OutputField(desc="Concrete next actions for mitigation and follow-up")
    time_range: str = dspy.OutputField(desc="Observed incident time range")
```

#### Grounded Answers with Citations

```python
from fleet_rlm.runtime.agent.signatures import GroundedAnswerWithCitations

class GroundedAnswerWithCitations(dspy.Signature):
    """Answer questions using chunked evidence and explicit citations.

    The model should ground each key claim in evidence from input chunks
    and produce machine-readable citations for downstream display.
    """
    query: str = dspy.InputField(desc="Question to answer from evidence")
    evidence_chunks: list[str] = dspy.InputField(desc="Relevant evidence chunks to ground the answer")
    response_style: str = dspy.InputField(desc="Response style preference such as concise or verbose")
    answer: str = dspy.OutputField(desc="Grounded answer synthesized from evidence")
    citations: list[dict[str, str]] = dspy.OutputField(
        desc="Citation dicts with keys: source, chunk_id, evidence, reason"
    )
    confidence: int = dspy.OutputField(desc="Estimated confidence score from 0 to 100")
    coverage_notes: str = dspy.OutputField(
        desc="Notes on coverage gaps or uncertainty in available evidence"
    )
```

#### Code Planning

```python
from fleet_rlm.runtime.agent.signatures import CodeChangePlan

class CodeChangePlan(dspy.Signature):
    """Generate a structured implementation plan for a code change."""
    task: str = dspy.InputField(desc="Requested coding task")
    repo_context: str = dspy.InputField(desc="Repository/domain context relevant to the task")
    constraints: str = dspy.InputField(desc="Constraints or non-goals to respect")
    plan_steps: list[str] = dspy.OutputField(desc="Ordered implementation steps")
    files_to_touch: list[str] = dspy.OutputField(desc="Likely files/modules that should be modified")
    validation_commands: list[str] = dspy.OutputField(
        desc="Commands to verify correctness before completion"
    )
    risks: list[str] = dspy.OutputField(desc="Key risks and failure modes to monitor")
```

#### Memory Operations

```python
from fleet_rlm.runtime.agent.signatures import (
    VolumeFileTreeSignature,
    MemoryActionIntentSignature,
    CoreMemoryUpdateProposal,
)

class VolumeFileTreeSignature(dspy.Signature):
    """Build a bounded, structured file-tree view for a volume path."""
    root_path: str = dspy.InputField(desc="Root path to traverse")
    max_depth: int = dspy.InputField(desc="Maximum directory depth to include")
    include_hidden: bool = dspy.InputField(desc="Whether to include hidden files")
    nodes: list[dict[str, str]] = dspy.OutputField(
        desc="Tree nodes with keys path, type, size_bytes, depth"
    )
    total_files: int = dspy.OutputField(desc="Total file count discovered")
    total_dirs: int = dspy.OutputField(desc="Total directory count discovered")
    truncated: bool = dspy.OutputField(
        desc="True when traversal stops early due to node limits"
    )

class MemoryActionIntentSignature(dspy.Signature):
    """Classify memory action intent and risk from user request + tree context."""
    user_request: str = dspy.InputField(desc="Original user request")
    current_tree: list[dict[str, str]] = dspy.InputField(desc="Current memory tree snapshot")
    policy_constraints: str = dspy.InputField(desc="Policy and safety constraints")
    action_type: str = dspy.OutputField(
        desc="Action type: read, write, append, move, delete, mkdir, tree, audit, migrate, noop"
    )
    target_paths: list[str] = dspy.OutputField(desc="Paths involved in the action")
    content_plan: list[str] = dspy.OutputField(desc="Planned content operations if applicable")
    risk_level: str = dspy.OutputField(desc="Risk level: low, medium, high")
    requires_confirmation: bool = dspy.OutputField(
        desc="Whether explicit confirmation should be required"
    )
    rationale: str = dspy.OutputField(desc="Why this action and risk were selected")
```

### Creating Custom Signatures

Create your own signatures following the same pattern:

```python
import dspy

class MyCustomSignature(dspy.Signature):
    """Describe your signature's purpose clearly.

    The docstring helps DSPy understand the task and is included in prompts.
    """
    input_field: str = dspy.InputField(desc="Description of input")
    output_field: str = dspy.OutputField(desc="Description of output")
```

Place custom signatures in `src/fleet_rlm/runtime/agent/signatures.py` to integrate with the runtime module registry.

## Module Construction

Modules wrap signatures with execution logic. Fleet-rlm provides factory functions for creating RLM-backed modules.

### Creating an RLM Instance

Use `create_runtime_rlm()` for canonical RLM construction:

```python
from fleet_rlm.runtime.models.rlm_runtime_modules import create_runtime_rlm
from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument
from fleet_rlm.runtime.execution.interpreter import DaytonaInterpreter

# Set up the Daytona interpreter
interpreter = DaytonaInterpreter(
    timeout=600,           # Sandbox timeout in seconds
    secret_name="LITELLM", # Modal secret containing API keys
    volume_name="my-vol",  # Optional: persistent Modal volume
    max_llm_calls=50,      # Limit LLM calls per session
)

# Create the RLM module
rlm = create_runtime_rlm(
    signature=SummarizeLongDocument,
    interpreter=interpreter,
    max_iterations=30,     # Max reasoning iterations
    max_llm_calls=50,      # Max LLM calls within RLM
    verbose=True,          # Print debug information
)

# Execute
result = rlm(
    document="Full text of document...",
    focus="What are the main themes?",
)
print(result.summary)
print(result.key_points)
```

### Using the Runtime Module Registry

For production use, prefer registry-based module construction:

```python
from fleet_rlm.runtime.models.rlm_runtime_modules import build_runtime_module
from fleet_rlm.runtime.execution.interpreter import DaytonaInterpreter

interpreter = DaytonaInterpreter(timeout=600, secret_name="LITELLM")

# Build by name (strings are validated against RUNTIME_MODULE_NAMES)
rlm = build_runtime_module(
    "summarize_long_document",
    interpreter=interpreter,
    max_iterations=30,
    max_llm_calls=50,
    verbose=True,
)
```

Available module names:

| Name                              | Signature                               | Purpose                                |
| --------------------------------- | --------------------------------------- | -------------------------------------- |
| `summarize_long_document`         | `SummarizeLongDocument`                 | Focused summarization                  |
| `extract_from_logs`               | `ExtractFromLogs`                       | Pattern extraction from logs           |
| `grounded_answer`                 | `GroundedAnswerWithCitations`           | Evidence-based answers with citations  |
| `triage_incident_logs`            | `IncidentTriageFromLogs`                | Incident diagnostics                   |
| `plan_code_change`                | `CodeChangePlan`                        | Implementation planning                |
| `propose_core_memory_update`      | `CoreMemoryUpdateProposal`              | Memory state updates                   |
| `memory_tree`                     | `VolumeFileTreeSignature`               | File tree traversal                    |
| `memory_action_intent`            | `MemoryActionIntentSignature`           | Action classification                  |
| `memory_structure_audit`          | `MemoryStructureAuditSignature`         | Structure auditing                     |
| `memory_structure_migration_plan` | `MemoryStructureMigrationPlanSignature` | Migration planning                     |
| `clarification_questions`         | `ClarificationQuestionSignature`        | Ambiguity resolution                   |

### Recursive Sub-Query RLM

For delegated sub-problems, use the recursive query pattern:

```python
from fleet_rlm.runtime.models.rlm_runtime_modules import build_recursive_subquery_rlm
from fleet_rlm.runtime.execution.interpreter import DaytonaInterpreter

interpreter = DaytonaInterpreter(timeout=300, secret_name="LITELLM")

rlm = build_recursive_subquery_rlm(
    interpreter=interpreter,
    max_iterations=30,
    max_llm_calls=50,
    verbose=True,
)

# Execute a sub-task
result = rlm(
    prompt="Analyze the error patterns in this log file",
    context="This is a Kubernetes pod log from a Node.js service",
)
print(result.answer)
```

## dspy.RLM Runtime Configuration

The `dspy.RLM` class extends DSPy with Daytona sandbox execution. Configure it through `DaytonaInterpreter`.

### Adapter Overrides

Structured runtime modules default to `JSONAdapter`, while non-runtime-module DSPy contexts use
 the optional `DSPY_ADAPTER` override when configured.

- `DSPY_STRUCTURED_OUTPUT_ADAPTER=chat|json|none`
- `DSPY_ADAPTER=chat|json|none`
- `DSPY_STRUCTURED_OUTPUT_ADAPTER_USE_NATIVE_FUNCTION_CALLING=true|false`
- `DSPY_ADAPTER_USE_NATIVE_FUNCTION_CALLING=true|false`

The native function-calling flags are experimental and remain off by default. They exist only as
 opt-in adapter prototypes and should not be enabled as the product default until streaming and
 trajectory compatibility are proven.

### DaytonaInterpreter Options

```python
from fleet_rlm.runtime.execution.interpreter import DaytonaInterpreter
from fleet_rlm.runtime.execution.profiles import ExecutionProfile

interpreter = DaytonaInterpreter(
    # Core settings
    timeout=900,                    # Total sandbox lifetime (seconds)
    secret_name="LITELLM",          # Modal secret for API keys
    volume_name="persistent-vol",   # Optional Modal volume for persistence

    # Execution limits
    max_llm_calls=100,              # Max LLM calls per session
    llm_call_timeout=120,           # Timeout per LLM call (seconds)
    execute_timeout=300,            # Timeout per code execution (seconds)
    idle_timeout=60,                # Sandbox idle timeout (seconds)

    # Execution profile
    default_execution_profile=ExecutionProfile.RLM_DELEGATE,

    # Async execution
    async_execute=True,             # Enable async sandbox operations

    # Debugging
    verbose=True,                   # Print debug information
)
```

### Execution Profiles

Fleet-rlm uses execution profiles to categorize sandbox behavior:

| Profile             | Purpose                             |
| ------------------- | ----------------------------------- |
| `RLM_DELEGATE`      | Child RLM for delegated sub-queries |
| `RLM_ROOT`          | Root RLM for primary execution      |
| `ROOT_INTERLOCUTOR` | Primary user-facing interaction     |
| `MAINTENANCE`       | Maintenance operations              |

Set the profile explicitly:

```python
from fleet_rlm import DaytonaInterpreter
from fleet_rlm.runtime.execution.profiles import ExecutionProfile

# During interpreter creation
interpreter = DaytonaInterpreter(
    ...,
    default_execution_profile=ExecutionProfile.RLM_DELEGATE,
)

# Or switch dynamically (context manager)
with interpreter.execution_profile(ExecutionProfile.RLM_ROOT):
    result = rlm(prompt="...")
```

### Streaming RLM Output

For real-time output, use `dspy.streamify`:

```python
import dspy
from dspy.streaming.streaming_listener import StreamListener

# Streamify an RLM module
stream_rlm = dspy.streamify(
    rlm,
    stream_listeners=[
        StreamListener(signature_field_name="answer")
    ],
    include_final_prediction_in_output_stream=True,
    is_async_program=True,
    async_streaming=True,
)

# Consume the stream
async for value in stream_rlm(prompt="Analyze this document", context="..."):
    if isinstance(value, dspy.Prediction):
        # Final prediction
        print(value.answer)
    else:
        # Streaming chunk
        print(value)
```

### Delegation from ReAct Agents

The `RLMReActChatAgent` delegates to child RLMs via the `rlm_query` tool:

```python
from fleet_rlm.runtime.agent.chat_agent import RLMReActChatAgent

agent = RLMReActChatAgent(
    react_max_iters=10,
    rlm_max_iterations=30,
    rlm_max_llm_calls=50,
    delegate_lm=None,  # Uses parent LM if not specified
    delegate_max_calls_per_turn=8,
    timeout=900,
    secret_name="LITELLM",
)

# The agent can use rlm_query tool for long-context tasks
result = agent.chat_turn("Analyze the architecture of this codebase")
```

Delegate configuration:

| Parameter                          | Purpose                                                        |
| ---------------------------------- | -------------------------------------------------------------- |
| `delegate_lm`                      | Optional separate LM for delegation (uses parent LM if `None`) |
| `delegate_max_calls_per_turn`      | Max delegate calls per chat turn                               |
| `delegate_result_truncation_chars` | Truncate delegate results longer than this                     |
| `rlm_max_iterations`               | Max iterations for delegate RLM                                |
| `rlm_max_llm_calls`                | Max LLM calls for delegate RLM                                 |

## MLflow Tracing Integration

Fleet-rlm integrates with MLflow for trace capture, feedback collection, and optimization. See [mlflow-workflows.md](mlflow-workflows.md) for complete documentation.

### Enable MLflow Tracing

```bash
export MLFLOW_ENABLED=true
export MLFLOW_TRACKING_URI=http://127.0.0.1:5001
export MLFLOW_EXPERIMENT=fleet-rlm
```

### Collect Traces During Execution

When MLflow is enabled, RLM execution automatically captures:

- Input/output pairs for each LLM call
- Tool invocations and results
- Reasoning trajectories
- Timing and token usage

### Optimize with MIPROv2

Use collected traces for DSPy optimization:

```bash
uv run python scripts/mlflow_cli.py optimize \
  --dataset artifacts/mlflow/annotated-traces.json \
  --program my_package.my_module:build_program \
  --input-key question \
  --output-key answer \
  --output artifacts/mlflow/optimized-program.json
```

## Best Practices

### Signature Design

1. **Use descriptive field names**: Names become part of the prompt
2. **Add docstrings**: The docstring explains the task to the LLM
3. **Specify field descriptions**: Use `desc=` for input/output fields
4. **Use typed outputs**: `list[str]`, `dict[str, str]` provide structure

### Module Construction

1. **Use the registry**: `build_runtime_module()` ensures consistency
2. **Configure timeouts appropriately**: Long documents need more time
3. **Set max_llm_calls**: Prevent runaway costs in recursive scenarios
4. **Share interpreters**: Reuse interpreters across related operations

### Execution

1. **Use async APIs**: `acall()` for async execution
2. **Handle streaming**: Process incremental output for long-running tasks
3. **Check budgets**: Monitor `max_llm_calls` consumption
4. **Enable tracing**: MLflow traces help debug and optimize

### Integration with Chat Agents

1. **Let the agent delegate**: `rlm_query` tool handles long-context tasks
2. **Configure depth limits**: `max_depth` prevents infinite recursion
3. **Monitor delegate calls**: Track `delegate_calls_turn` in metrics
4. **Use core memory**: Persistent context survives across turns

## Troubleshooting

### RLM Timeout Errors

If execution times out:

```python
# Increase timeout
interpreter = DaytonaInterpreter(timeout=1800)  # 30 minutes

# Or reduce iterations
rlm = create_runtime_rlm(
    ...,
    max_iterations=10,  # Fewer iterations
)
```

### Daytona Sandbox Connection Issues

Verify Daytona configuration:

```bash
uv run python scripts/validate_env.py daytona
```

### Memory Budget Exhausted

If LLM call budget is exhausted:

```python
# Increase budget
interpreter = DaytonaInterpreter(max_llm_calls=200)

# Or check current usage
used = interpreter._llm_call_count
remaining = interpreter.max_llm_calls - used
```

### Delegate Depth Exceeded

If recursive delegation hits depth limits:

```python
# Increase max depth
agent = RLMReActChatAgent(max_depth=4)

# Or simplify the task to reduce delegation
```

## Related Documentation

- [mlflow-workflows.md](mlflow-workflows.md) - MLflow tracing and optimization
- [../reference/daytona-runtime-architecture.md](../reference/daytona-runtime-architecture.md) - Daytona runtime configuration
- [runtime-settings.md](runtime-settings.md) - Server runtime settings
