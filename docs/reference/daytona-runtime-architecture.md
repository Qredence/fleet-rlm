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
  - `Daytona()` when environment-based configuration is sufficient
  - `Daytona(DaytonaConfig(...))` when an explicit resolved config override is required
- The sandbox adapter is async-first internally:
  - `AsyncDaytona` is the canonical sandbox runtime client
  - sandbox creation/resume, filesystem operations, volume lookup, session-command execution, and log streaming use the official async SDK surface
  - sync runtime/session methods are compatibility wrappers over the async implementation
- Persistent Daytona storage is modeled as a real Daytona volume:
  - volume lookup/creation uses `client.volume.get(volume_name, create=True)`
  - sandboxes attach that volume through `CreateSandboxFromSnapshotParams(... volumes=[VolumeMount(...)])`
- Live REPL/session progress uses the documented async session-command log streaming path rather than a custom transport:
  - the repo uses Daytona process sessions plus `get_session_command_logs_async(...)` for stdout/stderr follow mode
  - async session-command log streaming must stay on the owning request event loop; the runtime must not move the same `AsyncDaytona` client across loops or helper threads
  - sync compatibility wrappers fall back to snapshot polling via `get_session_command_logs(...)` instead of cross-thread async streaming
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
- `DaytonaWorkbenchChatAgent` remains only as a thin compatibility wrapper that
  configures the shared agent with Daytona-specific workspace/session metadata.
- The Daytona sandbox layer is now split internally:
  - `sandbox/__init__.py` preserves the stable `...daytona.sandbox` import surface
  - `sandbox/runtime.py` owns workspace bootstrap and context staging
  - `sandbox/session.py` owns the persistent REPL/session lifecycle
  - `sandbox/protocol.py` owns framed callback/event transport types
  - `sandbox/driver.py` holds the sandbox-side REPL driver source
  - `sandbox/sdk.py` owns Daytona SDK loading, client builders, and async/sync compatibility helpers
- Daytona volume browsing now lives in `volumes.py`, while `agent.py` and `state.py` replace the older top-level `chat_agent.py` / `chat_state.py` pair.

## Project-Specific Extensions

The repo intentionally extends Daytona's published guide shape with:

- a custom long-lived Python REPL bridge inside the sandbox instead of using
  Daytona's built-in code interpreter as the primary execution engine
- host callbacks for semantic and recursive subcalls
- prompt-handle storage and preview slicing
- richer websocket trace emission for the workspace transcript and canvas

These are intentional project behaviors, not alternative Daytona SDK semantics.

## Why The Interpreter Does Not Use `code_interpreter.run_code()`

The official async code interpreter supports stateful execution contexts and is
useful for simpler Python execution flows. The current `fleet-rlm-dspy`
Daytona interpreter does not use it as the primary execution engine because the
shared `dspy.RLM` runtime still requires sandbox-side capabilities that are
already implemented in the custom REPL bridge:

- host callback requests from sandbox to host
- structured execution events during one code block
- prompt-handle persistence and prompt-slice reads
- custom `SUBMIT(...)` final-artifact capture

This means the repo is intentionally hybrid:

- official async Daytona SDK for client, sandbox, volume, filesystem, and log-stream operations
- custom REPL protocol for the Daytona interpreter backend

If a future refactor can move those capabilities onto Daytona's built-in code interpreter cleanly, that should be treated as a separate architecture change rather than an incidental cleanup.

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

- Volatile REPL/session state:
  - Python globals, imports, helper functions, and in-memory objects live inside the long-running sandbox-side REPL process
  - this state persists across multiple host-loop `execute_code` calls only while that driver process remains alive
- Durable workspace memory:
  - files written to the mounted Daytona volume under `/home/daytona/memory`
  - this state is the persistence mechanism that survives driver restart, sandbox restart, or session resume

When code needs durable memory, it must write to the mounted Daytona volume rather than relying on in-process globals.
