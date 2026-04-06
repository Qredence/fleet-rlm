# Python API Reference

This page documents the current, maintained Python interfaces for building on `fleet-rlm`.

## Core Runtime Classes

### `fleet_rlm.DaytonaInterpreter`

Primary sandbox execution runtime. The maintained implementation lives in
`fleet_rlm.integrations.daytona.interpreter`.

Typical usage:

```python
from fleet_rlm import DaytonaInterpreter

with DaytonaInterpreter(timeout=600, volume_name="rlm-volume-dspy") as interp:
    result = interp.execute("print('hello')")
```

Key capabilities:

- lifecycle control (`start`, `shutdown`, context managers)
- sync/async execution (`execute`, `aexecute`)
- execution profile support used by server and delegate workflows

### `fleet_rlm.runtime.agent.chat_agent.RLMReActChatAgent`

Interactive ReAct orchestration module used by CLI and server chat surfaces.

Key behaviors:

- document loading and active-alias management
- command dispatch execution
- sync/async chat turn helpers
- streaming event generation for WebSocket clients

## Runner Functions (`fleet_rlm.cli.runners`)

Current maintained runner surface:

- `build_react_chat_agent(...)`
- `run_react_chat_once(...)`
- `arun_react_chat_once(...)`
- `run_long_context(...)`
### `build_react_chat_agent(...)`

Constructs an `RLMReActChatAgent` with runtime controls such as:

- ReAct/RLM iteration budgets
- recursion depth
- Daytona timeout/volume
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

- `summarize` → `SummarizeLongDocument`

## Signatures (`fleet_rlm.runtime.agent.signatures`)

Current maintained signatures include:

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
from fleet_rlm.cli.runners import run_long_context

result = run_long_context(
    docs_path="README.md",
    query="Summarize the architecture",
    mode="analyze",
)
print(result["answer"])
```

## Import Verification

```bash
uv run python -c "from fleet_rlm.cli.runners import run_long_context, run_react_chat_once"
uv run python -c "from fleet_rlm.runtime.agent.signatures import SummarizeLongDocument"
```
