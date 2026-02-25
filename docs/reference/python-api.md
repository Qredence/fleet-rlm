# Python API Reference

This page documents the current, maintained Python interfaces for building on `fleet-rlm`.

## Core Runtime Classes

### `fleet_rlm.core.interpreter.ModalInterpreter`

Primary sandbox execution runtime.

Typical usage:

```python
from fleet_rlm import ModalInterpreter

with ModalInterpreter(timeout=600, secret_name="LITELLM", volume_name="rlm-volume-dspy") as interp:
    result = interp.execute("print('hello')")
```

Key capabilities:
- lifecycle control (`start`, `shutdown`, context managers)
- sync/async execution (`execute`, `aexecute`)
- execution profile support used by server and delegate workflows

### `fleet_rlm.react.agent.RLMReActChatAgent`

Interactive ReAct orchestration module used by CLI and server chat surfaces.

Key behaviors:
- document loading and active-alias management
- command dispatch execution
- sync/async chat turn helpers
- streaming event generation for WebSocket clients

## Runner Functions (`fleet_rlm.runners`)

Current maintained runner surface:

- `build_react_chat_agent(...)`
- `run_react_chat_once(...)`
- `arun_react_chat_once(...)`
- `run_long_context(...)`
- `check_secret_presence(...)`
- `check_secret_key(...)`

### `build_react_chat_agent(...)`

Constructs an `RLMReActChatAgent` with runtime controls such as:
- ReAct/RLM iteration budgets
- recursion depth
- Modal timeout/secret/volume
- guardrail settings
- delegate LM settings

### `run_react_chat_once(...)` and `arun_react_chat_once(...)`

Single-turn wrappers around the interactive ReAct agent.

Common output shape includes:
- `assistant_response`
- optional trajectory metadata (when enabled)
- turn/session metadata and warnings

### `run_long_context(...)`

Long-document analysis/summarization helper backed by DSPy RLM signatures.

Modes:
- `analyze` → `AnalyzeLongDocument`
- `summarize` → `SummarizeLongDocument`

### Secret Diagnostics

- `check_secret_presence(secret_name="LITELLM")`
- `check_secret_key(secret_name="LITELLM", key="DSPY_LLM_API_KEY")`

These execute Modal-side checks for required environment keys.

## Signatures (`fleet_rlm.signatures`)

Current maintained signatures include:

- `AnalyzeLongDocument`
- `SummarizeLongDocument`
- `ExtractFromLogs`
- `GroundedAnswerWithCitations`
- `IncidentTriageFromLogs`
- `CodeChangePlan`
- `CoreMemoryUpdateProposal`
- `VolumeFileTreeSignature`
- `MemoryActionIntentSignature`
- `MemoryStructureAuditSignature`
- `MemoryStructureMigrationPlanSignature`
- `ClarificationQuestionSignature`

## Minimal Example

```python
from fleet_rlm.runners import run_long_context

result = run_long_context(
    docs_path="README.md",
    query="Summarize the architecture",
    mode="analyze",
)
print(result["answer"])
```

## Import Verification

```bash
uv run python -c "from fleet_rlm.runners import run_long_context, run_react_chat_once"
uv run python -c "from fleet_rlm.signatures import AnalyzeLongDocument"
```
