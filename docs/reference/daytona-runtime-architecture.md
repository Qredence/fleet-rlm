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
- Declarative Builder: [https://www.daytona.io/docs/en/declarative-builder](https://www.daytona.io/docs/en/declarative-builder)
- Log Streaming: [https://www.daytona.io/docs/en/log-streaming/](https://www.daytona.io/docs/en/log-streaming/)
- Volumes: [https://www.daytona.io/docs/en/volumes/](https://www.daytona.io/docs/en/volumes/)
- Recursive Language Models / DSPy: [https://www.daytona.io/docs/en/guides/recursive-language-models](https://www.daytona.io/docs/en/guides/recursive-language-models)

## What Is Directly Based On Daytona Docs

- Daytona clients are created through the official Python SDK entrypoints:
  - `from daytona import AsyncDaytona`
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
  - `llm_query` and `llm_query_batched` are semantic-only sandbox callbacks
  - `rlm_query` is the shared agent-level recursive entrypoint
  - `rlm_query_batched` is a Daytona-only agent-level recursive entrypoint for now
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
  - `runtime.py` owns workspace bootstrap, context staging, and snapshot helpers
  - `interpreter.py` owns the `dspy.RLM` interpreter backend and result translation
  - `bridge.py` owns the minimal host-callback broker used for `llm_query`, `llm_query_batched`, custom tools, and `SUBMIT(...)`
  - `diagnostics.py` owns structured Daytona diagnostics and smoke validation
  - `types.py` owns provider-local configuration, staged-context, smoke-result, and chat/session normalization contracts
  - `volumes.py` owns provider-specific volume browsing helpers
- `agent.py` remains the Daytona-specific agent/session adapter over the shared runtime.
- Recursive `rlm_query*` helpers are intentionally not sandbox callbacks in Daytona. Sandbox-authored code should use `llm_query` / `llm_query_batched`, while agent-level recursion remains outside the bridge.
- The provider is now async-first internally:
  - `AsyncDaytona` drives sandbox/session lifecycle
  - host-side volume browsing uses async Daytona helpers directly
  - async sandbox helpers such as `get_work_dir()` and `get_preview_link()` must be awaited before their values are used
  - owned `AsyncDaytona` clients should be closed when an interpreter/runtime is discarded to avoid leaking HTTP sessions
  - sync helper methods remain only as public compatibility shims over the async implementation
  - internal Daytona interpreter flow assumes the canonical async provider contract and does not probe for older sync-only runtime/session shapes
- Shared runtime control is intentionally split across three paths:
  - `RLMReActChatAgent` for ordinary user-facing interaction
  - recursive `dspy.RLM` child execution for deeper delegated work
  - cached runtime-module execution for non-recursive helper reuse
- Daytona's public heavy-work surface is intentionally limited to the named cached runtime-module capabilities plus `rlm_query` / `rlm_query_batched`. `parallel_semantic_map` is not part of the Daytona tool surface.
- `llm_query` / `llm_query_batched` remain available inside the Daytona interpreter, but they are internal semantic sub-primitives rather than peer public heavy-work tools. New Daytona heavy capabilities should use them only as a documented last resort.

## Session Continuity Model

The Daytona runtime now treats sandbox continuity as the default operating mode
for a chat session:

- one long-lived root Daytona sandbox session per agent session
- one persistent Daytona code-interpreter context reused across warm turns
- repo/ref/context changes reconcile in place inside that sandbox
- the mounted Daytona volume remains the canonical durable target for
  `memory/`, `artifacts/`, `buffers/`, and `meta/`

The runtime deliberately separates:

- sandbox identity: the long-lived Daytona sandbox and mounted volume
- workspace configuration: repo checkout, ref selection, staged
  `.fleet-rlm/context` inputs, and helper setup inside that sandbox

Repo, ref, or staged-context changes are no longer treated as automatic reasons
to delete the root sandbox. Instead, the runtime:

- clones a repo if the desired checkout is missing
- fetches and updates the checkout in place when the ref changes
- clears and restages `.fleet-rlm/context` when host context inputs change
- reruns sandbox helper setup when the workspace target changes so the live
  interpreter context retargets the new workspace path without discarding its
  in-memory state

The runtime only forces sandbox recreation when continuity would be unsafe or
incorrect:

- explicit session reset / `force_new_session`
- mounted volume incompatibility
- unrecoverable sandbox or reconcile failure
- resume failure for a persisted sandbox/context snapshot

This is the intended foundation for deeper `dspy.RLM` analysis flows: warm
turns continue in the same sandbox, durable outputs accumulate on the mounted
volume, and resumed sessions become a first-class continuity path instead of a
best-effort fallback.

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

- direct async Daytona SDK for client, sandbox, volume, filesystem, preview, process-session, and code-interpreter operations
- a minimal guide-style broker bridge for host callbacks only

## Workspace Volume Contract

- The Daytona persistent volume name is derived from the authenticated workspace/tenant claim.
- `DAYTONA_TARGET` is used only as Daytona SDK routing/config input.
- `DAYTONA_TARGET` must not be treated as a workspace id, sandbox id, or volume name.
- The current internal Daytona volume mount path is `/home/daytona/memory`.
- Session manifests on durable storage live under `meta/workspaces/<workspace_id>/users/<user_id>/react-session-<session_id>.json`.
- Manifest readers keep a best-effort fallback to the legacy `workspaces/...` path only for migration compatibility.
- Root and recursive child Daytona runs share the same workspace-scoped
  persistent volume when one is configured, while still using distinct Daytona
  sandbox sessions per child run.
- The runtime remains SDK-owned. Repo-side `.daytona`, devcontainer, or
  Declarative Builder config is not consulted at runtime in this iteration.
- Declarative Builder is relevant only as a future base-image/bootstrap strategy.

## Persistent Memory Model

There are two distinct persistence layers in the Daytona runtime:

- Volatile execution-context state:
  - Python globals, imports, helper functions, and in-memory objects live inside the Daytona code-interpreter context
  - this state persists across multiple `run_code(...)` calls while that context remains alive
- Durable mounted-volume storage:
  - the mounted volume root is `/home/daytona/memory`
  - canonical durable directories under it are `memory/`, `artifacts/`, `buffers/`, and `meta/`
  - session manifests and workspace provenance belong under `meta/workspaces/...`
  - workspace repos, staged context, package installs, caches, and scratch files are not durable by default
  - files survive context reset, sandbox restart, or session resume only when they are explicitly promoted into those durable directories

## Workspace vs. Volume vs. Context

- Workspace root: the live repo checkout plus transient execution files inside the sandbox
- Context root: run-scoped host inputs staged into the workspace under `.fleet-rlm/context`
- Mounted volume root: durable storage only, not a pseudo-persistent workspace

Workspace-aware tools target the live sandbox workspace. Volume-aware tools target the canonical durable directories. There is no automatic workspace-to-volume sync in this iteration.

When code needs durable memory or durable artifacts, it must explicitly write to the mounted Daytona volume rather than relying on in-process globals or transient workspace files.

Sandbox/file helper code should treat `DaytonaSandboxSession` as the canonical
interface:

- async flows use `aread_file`, `awrite_file`, and `alist_files`
- sync helpers use `_ensure_session_sync()` only at the public sync boundary
- helper code should not fall back to raw `sandbox.fs.*` access or mixed
  ad hoc session shapes

## Intentional Clean-Break Imports

- Deleted module paths such as `state.py`, `smoke.py`, and `snapshots.py` are intentionally unsupported.
- The canonical import path for the smoke result type is `fleet_rlm.integrations.providers.daytona.types.DaytonaSmokeResult`.
