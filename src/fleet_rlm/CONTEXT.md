# Fleet RLM backend

Canonical RLM-native backend glossary for conversation execution, isolation,
progressive Skills, and host-mediated files. Shared product terms (Sandbox,
Volume, Workspace, Skill) mean what root `CONTEXT.md` says unless narrowed here.

## Language

### Identity and conversation

**User**:
Stable process-local actor namespace for a Turn and ownership checks. The local
BYOK API supplies its deterministic identifier; callers do not authenticate or
select it.
_Avoid_: account, client

**Workspace**:
Stable process-local isolation namespace (same as shared). Owns Sessions,
Skills, Attachments, and Artifacts without an external tenant-membership claim.
_Avoid_: Sandbox Workspace

**Session**:
Durable chat conversation within a Workspace, with ordered Turns, Session
History, and a Checkpoint for concurrency.
_Avoid_: Sandbox, Run, Root Session

**Session History**:
Ordered durable user and assistant exchanges for a Session after successful
Committed Turns. Failed Runs do not append. Distinct from Code-Interpreter
Context and from Runtime Event logs.
_Avoid_: transcript wire log, REPL state, dspy.History (implementation type)

**Turn**:
One user message processed to a terminal outcome for a Session.
_Avoid_: request, job

**Committed Turn**:
A Turn whose durable Session History and Checkpoint match its terminal outcome.
A successful public terminal is only valid for a Committed Turn; success without
commit is a protocol violation, not a product state.
_Avoid_: stream completion alone, observed-complete, terminal event alone

**Run**:
The execution attempt for a Turn, identified for events, Run Cancellation, and
lease correlation.
_Avoid_: Turn (when meaning the user message), Sandbox

**Run Cancellation**:
An authorized request to stop a specific in-flight Run. Distinct from timeout
and Budget exhaustion. Ownership is re-checked. Outcome is one terminal Runtime
Event and never a successful Committed Turn for work that did not complete.
_Avoid_: disconnect alone, kill Sandbox (as the product act), Turn Cancellation

**Turn Claim**:
The right to execute or perform Idempotent Replay for a Turn under idempotency
and Checkpoint rules.
_Avoid_: lock alone, reservation

**Idempotent Replay**:
Re-delivery of a prior successful Committed Turn’s outcome as Runtime Events for
the same idempotency key, without a second Run (no second Root/Sub Model work,
no second Interpreter Lease). Not a new Committed Turn.
_Avoid_: new Run, client-side history fetch as the product name for this path

**Turn Commit**:
The durable application of a Run’s terminal outcome to Session History and
Checkpoint, producing a Committed Turn when the outcome is success.
_Avoid_: flush, save, persist (as the product outcome name)

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
request, Session History, Root Model, Sub Model, Budget, Interpreter Lease,
Skill Cards, Attachment references (and any Staged Attachments), and
Host-Mediated Tools as bound for the Run.
_Avoid_: HTTP request, Chat Execution Context (live term)

**Budget**:
Finite limits that bound one Run (iterations, Sub Model calls, wall time, skill
loads, tool calls).
_Avoid_: billing quota, plan limit

**Root Model**:
The model role that steers the recursive trajectory for a Run (generates and
revises interpreter work toward a final answer).
_Avoid_: primary LLM, chat model alone, Sub Model

**Sub Model**:
The model role that answers sub-queries issued from interpreter work during a
Run (e.g. bounded `llm_query`-style calls), distinct from the Root Model role
even when both use the same provider configuration.
_Avoid_: Root Model, helper model (vague), utility model (optional third role)

**Code-Interpreter Context**:
Python REPL state for one Run inside a Sandbox. Fleet RLM backend uses **per-Run**
Context only: it does not survive across Runs. Continuity between Runs is
Session History and Workspace Volume Scope, never REPL variables.
_Avoid_: durable Memory, Session History, warm multi-run REPL (not a clean claim)

**Interpreter Lease**:
Temporary right to use a Code-Interpreter Context for one Run. Release ends that
Context and never destroys the Sandbox by itself.
_Avoid_: Sandbox ownership, process handle alone, cross-Run REPL continuity

**Sandbox Binding**:
Remembered association between a Session and a Sandbox plus the mount identity
needed to reattach Workspace Volume Scope across Runs. It may retain the
Sandbox; it does not retain a Code-Interpreter Context across Runs.
_Avoid_: Interpreter Lease, Session record alone, warm REPL handle

**Workspace Volume Scope**:
The Workspace-exclusive portion of Volume storage available to that Workspace’s
Sandboxes. Another Workspace’s Sandbox must not list or read inside it. Sandbox
Binding must be able to reattach this scope on acquire.
_Avoid_: tenant Workspace alone, whole shared Volume root, Sandbox Workspace

### Skills and files

**Skill Card**:
Public discovery metadata for a Skill (name, description, trust, affordances)
without the instruction body.
_Avoid_: full Skill, prompt dump

**Skill**:
Host-held instruction and optional resources, loadable only after authorization.
_Avoid_: Skill Card, Memory

**Capability Package**:
Host-registered, executable contribution referenced by a Skill: tools, bounded
knowledge, typed task contract, serializable input adapters, validators, and
Budget requirements. Skill content cannot define executable host capabilities.
_Avoid_: arbitrary Skill code, plugin import, prompt body

**Turn Capability Blueprint**:
Immutable, host-validated composition for one Turn after optional Sub Model
selection of zero to four authorized Skills. It fixes the fresh RLM Signature,
tools, knowledge, adapters, validators, and primary task contract.
_Avoid_: mutable global tool registry, client-selected execution mode

**Task Contract**:
Host-registered typed DSPy Signature plus input mapper, structured-result
serializer, schema identity/version, and validator. At most one selected
primary Skill owns the Task Contract for a Turn.
_Avoid_: unvalidated model JSON, HTTP-provided Python type

**Serializable Input**:
Host-constructed `dspy.SandboxSerializable` value derived from an authorized
Attachment or dataset and reconstructed in the Run interpreter. Public HTTP
never accepts pickles or arbitrary Python objects.
_Avoid_: uploaded pickle, unrestricted object deserialization

**Host-Mediated Tool**:
A Run capability invocable from interpreter code whose authorization and side
effects are enforced on the host, not as unconstrained Sandbox-local code alone.
_Avoid_: free Sandbox helper, public HTTP route as the product concept

**Progressive Load**:
Fetching a Skill body or resource on demand during a Run via a Host-Mediated
Tool after re-authorization, subject to Budget.
_Avoid_: always-in-prompt skills

**Attachment**:
User-uploaded input file owned by User and Workspace, referenced by identity on
a Turn. Discovery for a Run uses identity and bounded metadata only; the body is
not an action-prompt payload and is read only through a Host-Mediated Tool.
_Avoid_: Artifact, Volume path as a public id, Attachment Card, prompt-embedded file

**Staged Attachment**:
An Attachment prepared for a specific Run inside Workspace Volume Scope so
interpreter code and Host-Mediated Tools can read it. Distinct from the durable
Attachment record. Public clients receive identity and metadata only—not host or
provider paths as product surface.
_Avoid_: public Sandbox path API, upload record alone, Artifact

**Artifact Candidate**:
Private output produced during a Run and awaiting Turn Commit. Its identity and
bytes are not public until promotion and the metadata transaction succeed.
_Avoid_: Artifact, Runtime Event, public creation response

**Artifact**:
Committed, publicly retrievable Run output owned by User and Workspace. An
Artifact exists only after its Artifact Candidate is promoted through Turn
Commit.
_Avoid_: Artifact Candidate, Attachment, Staged Attachment, log, transcript

### Public progress

**Runtime Event**:
Transport-neutral record of Run progress with exactly one terminal outcome for
observers. A successful terminal is only valid after Turn Commit; observers must
not be told success for a non-Committed Turn.
_Avoid_: SSE frame, WebSocket message, durability proof by itself

**RLM Detail**:
Sanitized model-authored reasoning, generated Python, interpreter output, or
tool activity produced during an RLM iteration. It is public product progress,
not provider-hidden chain-of-thought, a full prompt, or an unsanitized trace.
_Avoid_: hidden reasoning, raw provider trace, prompt dump

**UIMessage Stream**:
AI SDK UI 7 v1 SSE projection of Runtime Events for `useChat`. Successful
committed assistant Turns persist deterministic UIMessage parts; SSE bytes are
not themselves durable records.
_Avoid_: Runtime Event, raw event-log persistence

## Out of this context (for now)

These shared or live terms are **not** clean product claims until promoted:

- **Memory** as remember/recall runtime state
- **Child Session** / host delegation fan-out
- **Execution Mode** client switch (`simple` vs `rlm`)
- **Managed Target**, GEPA selection, and promotion artifacts
- Warm multi-run **Code-Interpreter Context** (clean is per-Run only)
- Optional third model role beyond Root Model and Sub Model
- Shared-root **Volume** without Workspace Volume Scope as a multi-tenant claim
