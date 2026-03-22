# Daytona Runtime Architecture

This note records the current Daytona integration boundary for `fleet-rlm-dspy`.
Daytona is now the sandbox/interpreter backend for the shared ReAct + `dspy.RLM`
runtime, not a separate chat/runtime orchestration system.

## Official Daytona Baseline

The current implementation treats these Daytona docs as the normative baseline:

- Python SDK: [https://www.daytona.io/docs/en/python-sdk/](https://www.daytona.io/docs/en/python-sdk/)
- Async Daytona: [https://www.daytona.io/docs/en/python-sdk/async/async-daytona/](https://www.daytona.io/docs/en/python-sdk/async/async-daytona/)
- Async Sandbox: [https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/](https://www.daytona.io/docs/en/python-sdk/async/async-sandbox/)
- Async File System: [https://www.daytona.io/docs/en/python-sdk/async/async-file-system/](https://www.daytona.io/docs/en/python-sdk/async/async-file-system/)
- Async Volume: [https://www.daytona.io/docs/en/python-sdk/async/async-volume/](https://www.daytona.io/docs/en/python-sdk/async/async-volume/)
- Async Code Interpreter: [https://www.daytona.io/docs/en/python-sdk/async/async-code-interpreter/](https://www.daytona.io/docs/en/python-sdk/async/async-code-interpreter/)
- Log Streaming: [https://www.daytona.io/docs/en/log-streaming/](https://www.daytona.io/docs/en/log-streaming/)
- Volumes: [https://www.daytona.io/docs/en/volumes/](https://www.daytona.io/docs/en/volumes/)
- Recursive Language Models / DSPy: [https://www.daytona.io/docs/en/guides/recursive-language-models](https://www.daytona.io/docs/en/guides/recursive-language-models)

## What Is Directly Based On Daytona Docs

- Daytona clients are created through the official Python SDK entrypoints:
  - `from daytona import Daytona`
  - `from daytona import DaytonaConfig`
- Sandbox bootstrap and resume use the native Daytona SDK surface directly:
  - `DaytonaSandboxRuntime` creates or resumes sandboxes
  - repo clone uses `sandbox.git.clone(...)`
  - local context staging uses `sandbox.fs.*`
- Persistent Daytona storage is modeled as a real Daytona volume:
  - volume lookup/creation uses `client.volume.get(volume_name, create=True)`
  - sandboxes attach that volume through `CreateSandboxFromSnapshotParams(... volumes=[VolumeMount(...)])`
- Stateful execution uses Daytona's built-in Python execution context:
  - `sandbox.code_interpreter.create_context(...)` provides persistent Python state
  - `sandbox.code_interpreter.run_code(...)` is the primary execution path for `DaytonaInterpreter`
- Process sessions are still used where Daytona's RLM guide needs a host-callback broker:
  - `sandbox.process.create_session(...)`
  - `sandbox.process.execute_session_command(...)`
  - `sandbox.get_preview_link(...)`
- Daytona-backed recursive work follows the guide's core invariants through the
  shared `dspy.RLM` path:
  - `RLMReActChatAgent` remains the top-level conversational runtime
  - long-context and recursive execution flow through `dspy.RLM`
  - `spawn_delegate_sub_agent_async` is the single recursive child-run path
  - `llm_query` is semantic-only and does not create child sandboxes
  - `rlm_query` and `rlm_query_batched` create true recursive Daytona child runs
  - each child run uses its own Daytona sandbox session and returns synthesized results to the parent

## Current Runtime Shape

- `modal_chat` and `daytona_pilot` now share the same ReAct + `dspy.RLM`
  runtime architecture.
- The backend difference is the interpreter implementation:
  - Modal uses `ModalInterpreter`
  - Daytona uses `DaytonaInterpreter`
- Websocket session switching must use the async agent/session reset path (`agent.areset(...)`) when clearing Daytona sandbox buffers for a fresh or restored session without saved state.
- `DaytonaWorkbenchChatAgent` remains the focused Daytona-specific agent layer
  that configures the shared runtime with Daytona workspace/session metadata.
- The Daytona provider now exposes its canonical implementation modules directly
  at the provider root:
  - `runtime.py` owns workspace bootstrap and context staging
  - `interpreter.py` owns the `dspy.RLM` interpreter backend and result translation
  - `bridge.py` owns the minimal host-callback broker used for `llm_query`, `llm_query_batched`, custom tools, and `SUBMIT(...)`
  - `types_budget.py`, `types_context.py`, `types_recursive.py`, `types_result.py`, and `types_serialization.py` own provider-local result, context, and recursion contracts
  - `volumes.py` owns provider-specific volume browsing helpers
- `agent.py` and `state.py` remain the Daytona-specific agent/session adapters over the shared runtime.
- Shared runtime control is intentionally split across three paths:
  - `RLMReActChatAgent` for ordinary user-facing interaction
  - recursive `dspy.RLM` child execution for deeper delegated work
  - cached runtime-module execution for non-recursive helper reuse

## Project-Specific Extensions

The repo intentionally extends Daytona's published guide shape with:

- a minimal sandbox-side broker server for host callbacks
- injected sandbox helpers for file reads, shell execution, and durable workspace/volume writes
- richer websocket trace emission for the workspace transcript and canvas
- result shaping that preserves the shared interpreter contract used by the rest of the backend

These are intentional project behaviors, not alternative Daytona SDK semantics.

## Why The Interpreter Uses `code_interpreter.run_code()`

The current `fleet-rlm-dspy` Daytona backend now follows the official DSPy/RLM
integration shape more closely:

- `sandbox.code_interpreter.run_code(...)` is the primary execution path
- the persistent Daytona context is the source of in-sandbox Python state
- a small broker process is started only when host callbacks are needed

This keeps the provider aligned with the Daytona SDK while preserving the extra
RLM contract the shared runtime still needs:

- host callback requests from sandbox to host
- custom tool dispatch
- custom `SUBMIT(...)` final-artifact capture
- stable result translation into the shared interpreter API

In practice the provider is intentionally hybrid:

- direct Daytona SDK for client, sandbox, volume, filesystem, preview, process-session, and code-interpreter operations
- a minimal guide-style broker bridge for host callbacks only

## Workspace Volume Contract

- The Daytona persistent volume name is derived from the authenticated workspace/tenant claim.
- `DAYTONA_TARGET` is used only as Daytona SDK routing/config input.
- `DAYTONA_TARGET` must not be treated as a workspace id, sandbox id, or volume name.
- The current internal Daytona volume mount path is `/home/daytona/memory`.
- Root and recursive child Daytona runs share the same workspace-scoped
  persistent volume when one is configured, while still using distinct Daytona
  sandbox sessions per child run.

## Persistent Memory Model

There are two distinct persistence layers in the Daytona runtime:

- Volatile execution-context state:
  - Python globals, imports, helper functions, and in-memory objects live inside the Daytona code-interpreter context
  - this state persists across multiple `run_code(...)` calls while that context remains alive
- Durable workspace memory:
  - files written to the mounted Daytona volume under `/home/daytona/memory`
  - this state is the persistence mechanism that survives context reset, sandbox restart, or session resume

When code needs durable memory, it must write to the mounted Daytona volume rather than relying on in-process globals.
