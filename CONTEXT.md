# Fleet-RLM Context

Fleet-RLM is a Daytona-backed recursive DSPy workbench. This glossary fixes the
project language used in docs, code review, and future agent sessions.

## Language

**Sandbox**:
An isolated Daytona compute environment with its own filesystem, process space,
network context, and lifecycle.
_Avoid_: workspace, volume, session

**Workspace**:
The live repo checkout, staged inputs, and execution files inside a sandbox.
_Avoid_: volume, durable memory

**Code-Interpreter Context**:
The persistent Python state associated with Daytona code execution inside a live
sandbox.
_Avoid_: durable state, memory

**Root Session**:
The primary Fleet-RLM runtime session for a user chat, backed by a root sandbox,
workspace, code-interpreter context, optional mounted volume, and session
metadata.
_Avoid_: child session, sandbox alone

**Child Session**:
A bounded delegated RLM session used for recursive work, normally backed by its
own child sandbox and deleted after the task.
_Avoid_: root session, shared workspace

**Volume**:
Mounted Daytona storage that can survive sandbox deletion when data is
explicitly written into its durable roots.
_Avoid_: workspace, code-interpreter context

**Memory**:
Reusable learned state, facts, preferences, or summaries stored for future
runtime use.
_Avoid_: transcript, log, artifact

**Artifact**:
A durable generated output such as Markdown, a report, JSON, or a file that
should remain inspectable after the sandbox that produced it is gone.
_Avoid_: log, scratch file, transcript

**Skill**:
A reusable runtime instruction or callable capability discovered from packaged
or volume-backed skill roots.
_Avoid_: memory, artifact

**Inline Context Payload**:
A large pasted block embedded directly in a user chat message, such as a
`CONTEXT:` section or full copied document.
_Avoid_: prompt context, model memory

**Staged Context**:
Large input data moved out of repeated action prompts and into RLM REPL
variables, typically `context["document_text"]`, `context["manifest"]`, and
`context["metadata"]`.
_Avoid_: hidden prompt, assistant scratchpad

**Shortened User Request**:
The prompt-facing instruction retained after an inline context payload is
staged. It names the task and tells the RLM where to inspect the full data.
_Avoid_: summary of the document, lossy context

**Log Event**:
A product-facing observability record for sandbox execution, process output,
bridge callbacks, volume/file activity, memory access, diagnostics, or runtime
progress.
_Avoid_: transcript message, artifact

**Execution Mode**:
The explicit per-turn contract a caller chooses for a chat turn, either
`simple` (one lightweight DSPy response, no tools/sandbox/recursion) or `rlm`
(Daytona-backed recursive execution with the full tool surface). The caller's
choice is honored with no auto-escalation.
_Avoid_: auto, route, escalation, tools_only, rlm_only (legacy aliases)

**RLMAgent**:
The first-class agent that owns the `rlm` execution path: it binds a Daytona
interpreter, selects among the standard/workspace/url-document RLM variants,
runs the retry/fallback resilience, and spawns sub-agents via `rlm_query()`.
_Avoid_: EscalatingFleetModule, FleetAgent, dispatcher

**ChatExecutionContext**:
The transport-neutral dataclass that carries prepared runtime dependencies,
identity, resolved session ids, a cancel flag, and per-turn `TurnControls`
into `stream_turn()`. Both the websocket and SSE transports build one from
their transport-specific inputs.
_Avoid_: WebSocket, request, SSE context

**TurnControls**:
The per-request control fields isolated inside `ChatExecutionContext`
(execution_mode, repo_url, repo_ref, context_paths, batch_concurrency,
docs_path, trace, trace_mode, selected_skill_ids). Distinct from prepared
runtime dependencies because they vary per message.
_Avoid_: options, params, flags

**RuntimeEvent**:
The transport-neutral event model yielded by `AgentRuntime.aiter_chat_turn_stream()`
and `stream_turn()`. Both transports project it to their wire format (WS
frames via `project_chat()`, SSE via the AI SDK UIMessage projector).
_Avoid_: frame, message, part
