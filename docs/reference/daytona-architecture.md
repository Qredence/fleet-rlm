# Daytona Runtime Architecture

This note records the current Daytona integration boundary for `fleet-rlm`.
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
  - `from daytona import Daytona`
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
  - `FleetAgent` (wrapped by `AgentRuntime`) remains the top-level conversational runtime
  - long-context and recursive execution flow through `dspy.RLM`
  - `delegate_to_rlm()` / `delegate_to_rlm_batched()` route through the single Daytona child-isolation path
  - `llm_query` and `llm_query_batched` are semantic-only sandbox callbacks
  - `rlm_query` is the shared agent-level recursive entrypoint
  - `rlm_query_batched` is a Daytona-only agent-level recursive entrypoint for now
  - each child run uses its own Daytona sandbox session and returns synthesized results to the parent

## Current Runtime Shape

- The public runtime is Daytona-backed and built on `AgentRuntime` plus the
  shared recursive DSPy runtime architecture.
- The maintained interpreter implementation is `DaytonaInterpreter`.
- Websocket session switching must use the async agent/session reset path (`agent.areset(...)`) when clearing Daytona sandbox buffers for a fresh or restored session without saved state.
- `AgentRuntime` (defaulting to `EscalatingFleetModule`) is the canonical shared DSPy agent and carries the
  Daytona workspace/session metadata needed by the workbench runtime.
- The Daytona provider now exposes its canonical implementation modules directly
  at the provider root:
  - `interpreter.py` is the public `DaytonaInterpreter` facade used by `dspy.RLM`, runtime services, notebooks, tests, and callers.
  - `runtime.py` owns Daytona SDK client construction, sandbox creation/resume/fork, snapshot/image selection, volume mounting, and sandbox concurrency slot accounting.
  - `workspace_manager.py` owns workspace config, session lifecycle, persisted Daytona state, runtime metadata, workspace reconciliation, and session import/export.
  - `session_runtime.py` owns the live sandbox session object, code-interpreter context lifecycle, file helpers, delete/archive/recover operations, and metadata refresh.
  - `sandbox_executor.py` owns code sanitization, injected sandbox helpers, direct and bridged execution, callback handoff, stdout/stderr capture, and result finalization.
  - `bridge.py` owns the host-callback broker used for `llm_query`, `llm_query_batched`, `sub_rlm`, `sub_rlm_batched`, custom tools, evidence helpers, and `SUBMIT(...)`.
  - `isolation.py` owns recursive child policy/delegation, child sandbox isolation, host-mediated evidence persistence, and local context staging.
  - `volumes.py` owns volume readiness, mounted-root layout, volume browsing, memory DB bootstrap, seeded skills, and lower-level volume file operations.
  - `_repo.py` owns git ref resolution, repo checkout/reconcile, and local context staging helpers.
  - `config.py` owns Daytona config resolution, lazy SDK imports, env loading, and SDK error classification.
  - `models.py` owns sandbox specs, workspace config, staged-context records, smoke results, chat/session normalization contracts, and durable state DTOs.
  - `snapshots.py` owns reusable Daytona snapshot/image bootstrap support.
  - `concurrency.py` owns sandbox slot limits, usage stats, and slot release accounting.
  - `diagnostics.py` owns structured Daytona diagnostics and smoke validation.
  - `log_stream.py` owns raw sandbox log classification; it is not yet a complete Daytona process-log streaming adapter.
- Daytona adapter modules may import the Daytona SDK directly. Higher-level
  runtime, API, and frontend code should use Fleet-RLM interfaces such as
  `DaytonaInterpreter`, `DaytonaSandboxRuntime`, `DaytonaSandboxSession`, and
  runtime event DTOs so config, diagnostics, lifecycle policy, redaction, and
  durable-state rules stay centralized.
- Lazy Daytona imports and config wrappers are intentional unless a concrete
  simplification proves they no longer protect import time, optional setup,
  diagnostics, or testability. Pydantic v2 is used for normalized
  configuration/state inputs such as `WorkspaceConfig`; hot execution-path
  carriers such as `DaytonaExecutionResponse` remain lightweight
  dataclasses/functions.
- Recursive `rlm_query*` helpers are intentionally not sandbox callbacks in Daytona. Sandbox-authored code should use `llm_query` / `llm_query_batched`, while agent-level recursion remains outside the bridge.
- The Fleet-facing provider contract is async-first:
  - `DaytonaInterpreter.astart()`, `ashutdown()`, `aexecute()`, `aconfigure_workspace()`, and `aimport_session_state()` are real coroutines
  - `DaytonaSandboxSession` file/lifecycle `a*` methods are real coroutines for API services that await them
  - sync helper methods remain public compatibility shims for notebooks, tests, and direct Python API users
  - internal workspace manager code prefers async collaborators and can fall back to sync compatibility methods at test or adapter boundaries
- Sandbox creation prefers the reusable `fleet-rlm-base` Daytona snapshot. That
  snapshot is an environment template built from a declarative image containing
  fleet-rlm's default Python runtime packages (`dspy`, `numpy`, `pandas`,
  `httpx`, and `pydantic`). If the snapshot is missing or not active, sandbox
  creation falls back to the same declarative image build so startup still has
  the expected dependencies. Operators can bootstrap or refresh the template
  with `uv run fleet-rlm daytona-snapshot`.
- Shared runtime control is intentionally split across three paths:
  - `AgentRuntime` for ordinary user-facing interaction
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
  `memory/`, `memories/`, `knowledge/`, `skills/`, `sessions/`, `logs/`,
  `artifacts/`, `buffers/`, `uploads/`, and `meta/`

The runtime deliberately separates:

- sandbox identity: the isolated Daytona compute environment
- root session: the long-lived Fleet-RLM runtime binding to a root sandbox,
  code-interpreter context, workspace path, optional mounted volume, and
  session metadata
- child/delegated session: a bounded recursive run that uses a separate child
  sandbox by default and returns synthesized results to the parent
- workspace configuration: repo checkout, ref selection, staged
  `.fleet-rlm/context` inputs, and helper setup inside a sandbox
- code-interpreter context: persistent Python state inside a live sandbox; it
  is useful operational state, not durable storage
- volume: mounted Daytona storage that survives sandbox deletion when writes
  are explicitly promoted into the durable roots
- memory: reusable facts, preferences, and learned state stored on the durable
  volume, not merely transcript text
- artifact: a durable generated output such as Markdown, reports, JSON, or
  files that should remain inspectable after sandbox deletion
- skill: a reusable runtime instruction or callable capability loaded from
  scaffolded or volume-backed skill roots
- log/event: live and persisted observability records used by chat, sidepanel,
  diagnostics, and future trace-based learning

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

Root and child lifecycle rules are different by design:

- root sandboxes can pause, resume, archive, or delete depending on configured
  policy, explicit user action, provider state, and session compatibility
- child sandboxes remain delete-after-task by default to bound cost and prevent
  accidental state leakage across recursive work
- important child outputs must be returned through the RLM result or promoted
  to durable volume storage before the child sandbox is deleted
- transient workspace files, interpreter globals, process buffers, and running
  processes must not be treated as durable state unless a tool explicitly saves
  them to the mounted volume or another durable store

## Project-Specific Extensions

The repo intentionally extends Daytona's published guide shape with:

- a minimal sandbox-side broker server for host callbacks
- injected sandbox helpers for file reads, shell execution, and durable workspace/volume writes
- richer websocket trace emission for the workspace transcript and sidepanel
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

- direct Daytona SDK usage for client, sandbox, volume, filesystem, preview, process-session, and code-interpreter operations inside the Daytona adapter
- a minimal guide-style broker bridge for host callbacks only

## Workspace Volume Contract

- The Daytona persistent volume name is derived from the authenticated workspace/tenant claim.
- `DAYTONA_TARGET` is used only as Daytona SDK routing/config input.
- `DAYTONA_TARGET` must not be treated as a workspace id, sandbox id, or volume name.
- The current internal Daytona volume mount path is `/home/daytona/memory`.
- Session manifests on durable storage live under `sessions/<session_id>/conversation.json`.
- Manifest readers use the current session conversation path only.
- Root and recursive child Daytona runs share the same workspace-scoped
  persistent volume when one is configured, while still using distinct Daytona
  sandbox sessions per child run.
- The runtime remains SDK-owned. Repo-side `.daytona`, devcontainer, or
  Declarative Builder config is not consulted at runtime in this iteration.
- Declarative Builder is relevant only as a future base-image/bootstrap strategy.

## Persistent Memory Model

The Daytona runtime separates the reusable environment template from its two
persistence layers:

- Reusable environment template:
  - `fleet-rlm-base` is a Daytona snapshot used only to speed up sandbox
    creation by pre-baking the default runtime packages
  - it is not a chat/session persistence mechanism, and it does not capture the
    live filesystem state of an active Fleet session
- Volatile execution-context state:
  - Python globals, imports, helper functions, and in-memory objects live inside the Daytona code-interpreter context
  - this state persists across multiple `run_code(...)` calls while that context remains alive
- Durable mounted-volume storage:
  - the mounted volume root is `/home/daytona/memory`
  - canonical durable directories under it are `memory/`, `memories/`,
    `knowledge/`, `skills/`, `sessions/`, `logs/`, `artifacts/`, `buffers/`,
    `uploads/`, and `meta/`
  - session manifests belong under `sessions/<session_id>/conversation.json`
  - workspace repos, staged context, package installs, caches, and scratch files are not durable by default
  - files survive context reset, sandbox restart, or session resume only when they are explicitly promoted into those durable directories

Transcript persistence is necessary but not enough for long-running and
self-improving RLM workflows. Saved turns can restore conversation continuity,
but reusable agent behavior should come from explicit durable memory, selected
skills, persisted knowledge, trace/evaluation records, durable logs, and
artifact lineage. The volume-backed `remember` / `recall` tools provide a
manual operational memory path today; future automatic recall or memory
consolidation must keep scopes, provenance, and child-write policy explicit.

Markdown and report artifacts should be treated as durable product outputs.
The current volume browser can preview Markdown files; future chat and
sidepanel work should emit artifact events that reference durable volume paths
so generated reports can be shown inline in chat and rendered in the sidepanel
after the originating sandbox is gone.

Event and log streaming is product UX infrastructure, not just debug output.
Events emitted to the frontend should carry correlation identifiers when
available, including run, session, sandbox, child sandbox, process, command,
tool, artifact, memory, actor, and parent event ids. Secrets, tokens,
environment values, and provider credentials must be redacted before frontend
emission or durable log persistence.

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

- Deleted legacy module paths such as `workspace_runtime.py`, `sdk_ops.py`,
  `bridge_callbacks.py`, `state.py`, `smoke.py`, and
  `integrations/daytona/async_compat.py` are intentionally unsupported.
- Snapshot support now lives in `integrations/daytona/snapshots.py`; do not
  treat that module as a deleted legacy path.
- The canonical import path for the smoke result type is
  `fleet_rlm.integrations.daytona.models.DaytonaSmokeResult`.

## Required References For Future Edits

Before changing Daytona integration code, use the local Daytona skill and this
document. Before changing module seams or ownership, use the codebase-design
skill. Before reviewing or landing broad changes, use the code-review skill.
When edits touch FastAPI routes, DSPy/RLM behavior, or shadcn/frontend UI, also
consult the corresponding FastAPI, DSPy, and shadcn docs or skills before
modifying code.
