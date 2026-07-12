# Clean Backend

Parallel RLM-native backend until cutover. This glossary names conversation
execution, isolation, progressive skills, and host-mediated files for that
package. Shared product terms (Sandbox, Volume, Workspace, Skill) mean what
root `CONTEXT.md` says unless narrowed here.

## Language

### Identity and conversation

**User**:
Authenticated principal for a Turn and for ownership checks.
_Avoid_: account, client

**Workspace**:
Tenant isolation scope (same as shared). Owns Sessions, Skills, Attachments,
and Artifacts for members.
_Avoid_: Sandbox Workspace

**Session**:
Durable chat conversation within a Workspace, with ordered Turns and a
Checkpoint for concurrency.
_Avoid_: Sandbox, Run, Root Session

**Turn**:
One user message processed to a terminal outcome for a Session.
_Avoid_: request, job

**Run**:
The execution attempt for a Turn, identified for events, cancel, and lease
correlation.
_Avoid_: Turn (when meaning the user message), Sandbox

**Turn Claim**:
The right to execute or replay a Turn under idempotency and Checkpoint rules.
_Avoid_: lock alone, reservation

**Checkpoint**:
Monotonic Session version used to detect stale concurrent commits.
_Avoid_: git commit, file snapshot

### Execution

**Chat Turn Command**:
Validated turn intent after identity resolution: who, which Session, message,
and optional Attachment references.
_Avoid_: raw HTTP body

**Turn Context**:
Everything required to execute one recursive Turn after isolation: identity,
request, models, Budget, Interpreter Lease, and optional hosts.
_Avoid_: HTTP request, Chat Execution Context (live term)

**Budget**:
Finite limits that bound one Run (iterations, sub-LM calls, wall time, skill
loads, tool calls).
_Avoid_: billing quota, plan limit

**Interpreter Lease**:
Temporary right to use a code-interpreter handle for a Run; release does not
destroy the Sandbox.
_Avoid_: Sandbox ownership, process handle alone

**Sandbox Binding**:
Remembered association between a Session and a Sandbox (and volume mount
identity) for reuse across Runs.
_Avoid_: Interpreter Lease, Session record alone

### Skills and files

**Skill Card**:
Public discovery metadata for a Skill (name, description, trust, affordances)
without the instruction body.
_Avoid_: full Skill, prompt dump

**Skill**:
Host-held instruction and optional resources, loadable only after authorization.
_Avoid_: Skill Card, Memory

**Progressive Load**:
Fetching a Skill body or resource on demand during a Run after re-authorization,
subject to Budget.
_Avoid_: always-in-prompt skills

**Attachment**:
User-uploaded input file owned by User and Workspace, referenced by identity on
a Turn.
_Avoid_: Artifact, Volume path as a public id

**Artifact**:
Generated output retained for inspection after the Run, owned by User and
Workspace.
_Avoid_: Attachment, log, transcript

### Public progress

**Runtime Event**:
Transport-neutral record of Run progress with exactly one terminal outcome for
observers.
_Avoid_: SSE frame, WebSocket message

## Out of this context (for now)

These shared or live terms are **not** clean product claims until promoted:

- **Memory** as remember/recall runtime state
- **Child Session** / host delegation fan-out
- **Execution Mode** client switch (`simple` vs `rlm`)
- **Managed Target**, GEPA selection, and promotion artifacts
