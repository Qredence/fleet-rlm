# Fleet RLM Context

Fleet RLM is a Daytona-backed recursive DSPy workbench. This glossary fixes the
implemented product language used across packages, docs, and agent sessions.

## Identity and conversation

**User**:
A deterministic process-local actor namespace for the single-user BYOK API. It
supports ownership checks; it is not caller-supplied authentication.
_Avoid_: account, client, customer

**Workspace**:
A deterministic process-local isolation namespace that owns Sessions, Skills,
Attachments, and Artifacts. It is not an external tenant or a Sandbox checkout.
_Avoid_: Sandbox Workspace, repo checkout, Volume

**Session**:
A durable conversation for the local User within the Workspace, with ordered
Turns and concurrency-safe history.
_Avoid_: Sandbox, Run, transcript alone

**Turn**:
One user message processed to a terminal outcome within a Session.
_Avoid_: request, job, Run when referring to the user message

**Run**:
One execution attempt of a Turn, including preparation, progress, cancellation,
completion, and cleanup.
_Avoid_: Turn, Session, Sandbox

## Compute and storage

**Run Environment**:
The explicitly selected execution profile. The public set contains only
`daytona`; private deterministic test composition is not a public profile.
_Avoid_: fallback tier, compatibility mode

**Sandbox**:
An isolated Daytona compute environment with its own filesystem, processes,
network context, and lifecycle.
_Avoid_: Workspace, Volume, Session

**Sandbox Workspace**:
The live execution files and staged inputs inside a Sandbox.
_Avoid_: Workspace namespace, Volume, Session Workspace

**Interpreter Context**:
The Python state reused across interpreter calls within one Run. Each later Run
receives a fresh context; it is not durable Session state.
_Avoid_: Memory, Volume contents, cross-Run REPL

**Workspace Volume Scope**:
The Workspace-owned subtree of mounted Daytona storage used for durable
Attachments, Artifacts, Workspace Memory, private Session files, and bounded Run
derivatives.
_Avoid_: whole provider Volume, Sandbox filesystem

**Workspace Memory**:
Daytona-only workspace-wide immediate state stored in the fixed `MEMORIES.md`
file at the root of the mounted Workspace Volume Scope. The RLM reads the
bounded newest records on demand through a host-mediated Tool and appends one
  record only when the user explicitly asks to remember something. Appends are
  immediately durable, independent of Turn Commit, and survive failed or
  cancelled Runs and Sandbox replacement. Append serialization is
  process-local: one Fleet host writes `MEMORIES.md`; concurrent multi-process
  append is not coordinated.
_Avoid_: Session History, automatic Turn-start recall, unbounded learned state

**Workspace Volume Tree**:
A Daytona-only bounded read-only logical listing of relative paths in the
process-local Workspace Volume, available through the HTTP API and terminal
client. It does not expose file contents, provider paths, mutation operations,
or a general-purpose Sandbox filesystem browser.
_Avoid_: Volume mount API, Sandbox browser, public storage paths

**Session Workspace**:
Immediate private text-file state under `sessions/{session_id}/workspace/` in
Workspace Volume Scope. It survives failed Runs and Sandbox replacement but is
not committed conversation history or a public Artifact.
_Avoid_: tenant Workspace, Sandbox Workspace, Artifact Candidate

**Attachment**:
A user-provided input file owned by the Workspace and referenced by a Turn.
_Avoid_: Artifact, Session Workspace file

**Artifact Candidate**:
A private generated Run output pending byte validation, promotion, and Turn
Commit.
_Avoid_: committed Artifact, arbitrary Sandbox file

**Artifact**:
A committed, authorized output with durable metadata and integrity-checked
content.
_Avoid_: log, scratch file, Attachment, uncommitted candidate

**Result Snapshot**:
An optional private `result.json` derivative for a successful Daytona Run. It is
commit-gated but is not a public Artifact or replay source.
_Avoid_: Committed Turn, Artifact

## Skills and execution

**Skill**:
A versioned instruction package with bounded discovery metadata, a `SKILL.md`
body, and optional declared resources. Instructions load progressively.
_Avoid_: host Tool, Memory, Artifact

**Skill Card**:
The bounded Skill identity, version, name, and description exposed for discovery
or passed to the RLM before full instructions load.
_Avoid_: complete Skill body, executable capability

**Host Capability**:
A trusted host-owned executable capability registered as an explicit
`dspy.Tool`. It is separate from Skill Markdown and is not supplied by HTTP.
_Avoid_: Skill resource, arbitrary callable from a request

**Task Contract**:
A host-declared DSPy Signature and bounded input/output policy selected during
Turn preparation.
_Avoid_: caller-provided schema, serialized executable

**Runtime Event**:
The closed transport-neutral record of Run progress and completion. The SSE
layer projects Runtime Events into AI SDK UI chunks.
_Avoid_: wire frame, durable Turn part

**Tool Event View**:
The host-owned allowlist that projects safe bounded Tool metadata. A Tool with no
view exposes identity, name, status, and a fixed failure message only.
_Avoid_: heuristic redaction, raw arguments/results

**Committed Turn**:
The versioned durable semantic aggregate used for Session replay.
_Avoid_: live trajectory buffer, result snapshot

**Turn Commit**:
The atomic successful finalization performed by `TurnLifecycle.finish()` after
result validation and Artifact byte promotion.
_Avoid_: SSE completion, candidate creation

**Interpreter Lease**:
The Run-scoped ownership handle for a Daytona interpreter/Sandbox binding. It is
held through finalization and released during coordinator cleanup.
_Avoid_: Session-owned Sandbox, reusable global interpreter

## Reserved, not current product behavior

The following terms describe possible future work and must not be presented as
implemented: **Root Session**, **Child Session**, automatic or unbounded learned
**Memory** beyond the fixed Daytona Workspace Memory contract, cross-Run
interpreter state, caller-selectable **Execution Mode**, `RLMAgent`, large-input
staging modes, **Chat Execution Context**, and public **Turn Controls**.
Introduce any of them only through a separate product and architecture
decision.
