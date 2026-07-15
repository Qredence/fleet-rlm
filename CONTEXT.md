# Fleet-RLM Context

Fleet-RLM is a Daytona-backed recursive DSPy workbench. This glossary fixes the
shared product language used across packages, docs, and agent sessions.

## Language

### Identity and conversation

**User**:
A stable process-local actor namespace for the single-user BYOK API. Its
deterministic identifier supports ownership checks; it is not caller-supplied
or proof of authentication.
_Avoid_: account, client, customer

**Workspace**:
A stable process-local isolation namespace that owns Sessions, Skills,
Attachments, and Artifacts. Its deterministic identifier is not an external
tenant or membership claim.
_Avoid_: Sandbox Workspace, repo checkout, Volume, Sandbox filesystem

**Session**:
A durable chat conversation for a User within a Workspace, with ordered Turns
and concurrency-safe history.
_Avoid_: Sandbox, Run, transcript alone

**Turn**:
One user message processed to a terminal outcome within a Session.
_Avoid_: request, job, Run (when meaning the user message)

**Run**:
One execution attempt of a Turn, including progress, cancel, and completion.
_Avoid_: Turn, Session, Sandbox

**Root Session**:
A Session that owns a long-lived root Sandbox, Sandbox Workspace,
Code-Interpreter Context, and optional Volume for ongoing chat.
_Avoid_: Child Session, Sandbox alone

**Child Session**:
A bounded delegated session used for recursive work, normally backed by its own
child Sandbox and discarded after the task.
_Avoid_: Root Session, tenant Workspace

### Compute and storage

**Sandbox**:
An isolated Daytona compute environment with its own filesystem, process space,
network context, and lifecycle.
_Avoid_: Workspace, Volume, Session

**Sandbox Workspace**:
The live repo checkout, staged inputs, and execution files inside a Sandbox.
_Avoid_: Workspace (tenant), Volume, durable Memory

**Code-Interpreter Context**:
The persistent Python state associated with code execution inside a live Sandbox.
_Avoid_: durable Memory, Volume contents, Session history

**Volume**:
Mounted Daytona storage that can survive Sandbox deletion when data is
explicitly written into its durable roots.
_Avoid_: Sandbox Workspace, Code-Interpreter Context, tenant Workspace

**Memory**:
Reusable learned state, facts, preferences, or summaries stored for future
runtime use.
_Avoid_: transcript, log, Artifact, Attachment

**Attachment**:
A user-provided input file owned within a Workspace and referenced on a Turn.
_Avoid_: Artifact, Volume file, Memory

**Artifact**:
A durable generated output that should remain inspectable after the Sandbox
that produced it is gone.
_Avoid_: log, scratch file, transcript, Attachment

**Skill**:
A reusable runtime instruction or capability package available to authorized
Turns.
_Avoid_: Memory, Artifact, Attachment

### Large inputs

**Inline Context Payload**:
A large pasted block embedded directly in a user chat message.
_Avoid_: prompt context, model memory, Attachment

**Staged Context**:
Large input data moved out of repeated action prompts into REPL variables for
programmatic inspection.
_Avoid_: hidden prompt, assistant scratchpad

**Shortened User Request**:
The prompt-facing instruction retained after an Inline Context Payload is
staged. It names the task and where to inspect the full data.
_Avoid_: summary of the document, lossy context

### Progress and (live) turn contracts

**Log Event**:
A product-facing observability record for sandbox execution, process output,
volume or file activity, memory access, diagnostics, or runtime progress.
_Avoid_: transcript message, Artifact

**Runtime Event**:
The transport-neutral record of Run progress and completion used by
observability and by transports that project a transcript.
_Avoid_: frame, wire message, part

**Execution Mode**:
The explicit per-turn contract a caller may choose on the live product path:
lightweight single-shot response versus Daytona-backed recursive execution.
_Avoid_: auto, route, escalation (as silent substitutes for an explicit choice)

**RLMAgent**:
The live product agent role that owns recursive Daytona-backed execution for a
Turn, including interpreter binding and delegated child work.
_Avoid_: EscalatingFleetModule, FleetAgent, dispatcher

**Chat Execution Context**:
Transport-neutral prepared dependencies and identity for executing a live Turn.
_Avoid_: raw HTTP request, wire-only session bag

**Turn Controls**:
Per-message controls for a live Turn (mode, repo, paths, skills, tracing)
distinct from long-lived prepared runtime dependencies.
_Avoid_: options, params, flags
