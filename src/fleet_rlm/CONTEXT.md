# Fleet RLM backend

Canonical RLM-native backend glossary for conversation execution, isolation,
progressive Skills, Workspace Memory, and host-mediated files. Shared product
terms (Sandbox, Volume, Workspace, Skill) mean what root `CONTEXT.md` says unless
narrowed here.

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

**Session Context Manifest**:
The bounded RLM input projection of one Session checkpoint: Session identity,
committed message count, and short previews of at most the six most recent
messages. Older canonical content remains in Session History and is read through
the Session-scoped Host-Mediated Tool.
_Avoid_: complete transcript, summary, automatic durable Memory

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
An authorized request to stop a specific in-flight Run. Distinct from timeout.
Ownership is re-checked. Outcome is one terminal Runtime
Event and never a successful Committed Turn for work that did not complete.
Once settlement completes, the cancelled attempt still persists a bounded
tombstone Committed Turn (status marker, observed usage, closed text) so
committed history shows the attempt without evidence parts.
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
`RunLifecycle.finish()` owns this boundary after typed-result validation and
Artifact byte promotion.
_Avoid_: flush, save, persist (as the product outcome name)

**Checkpoint**:
Monotonic Session version used to detect stale concurrent commits.
_Avoid_: git commit, file snapshot

### Execution

**Chat Turn Command**:
Validated turn intent after identity resolution: who, which Session, message,
optional Attachment references, and up to four exact Skill selections.
_Avoid_: raw HTTP body

**Turn Context**:
Everything required to execute one recursive Turn after isolation: identity,
request, host-owned Session History, bounded Session Context Manifest, Root
Model, Sub Model, RLM Options, Turn Timeout deadline, Interpreter Lease, Skill
Cards, Attachment references (and any Staged Attachments), and Host-Mediated
Tools as bound for the Run.
_Avoid_: HTTP request, Chat Execution Context (live term)

**RLM Options**:
The native DSPy limits for one Root or Recursive Child invocation. The default
Root values mirror DSPy 3.3.x: `max_iters = 20` for action/REPL iterations,
`max_llm_calls = 50` for prompts sent through native
`llm_query`/`llm_query_batched`, and `max_output_chars = 10000` for retained REPL
output. Fleet child defaults are smaller (`8`, `12`, and `4000` respectively).
These limits are independent of recursive depth; Turn timeout, interpreter
output caps, recursive call budgets, and payload-size limits are separate
operational controls.
_Avoid_: Fleet budget ledger, billing quota, plan limit

**Observed LM Usage**:
Potentially incomplete provider telemetry exposed under `observed_lm_usage` after
a completed Prediction. Its closed public policy admits only token, cache, and
cost measurements; unknown provider fields are dropped. Fleet does not expose
retry or call counters and does not infer missing provider or recursive-call
counters. Trace/delegation output distinguishes `token_usage_status` as
`observed` from `unavailable`; absent provider usage emits no misleading
all-zero aggregate.
_Avoid_: estimated calls, remaining calls, complete billing record

**Turn Timeout**:
The overall wall-clock limit for one Turn, represented internally by one
absolute deadline shared with Daytona Admission. It is not an RLM Option.
_Avoid_: LM call limit, Daytona Admission timeout, payload-size limit

**Owned Effect**:
Already-started asynchronous work retained by the caller that started it. The
provider-neutral `runtime.OwnedEffect` seam shields settlement from waiter
cancellation, preserves the terminal result or exception, and may return a
bounded pending wait without cancelling the effect. Run lifecycle, RLM worker,
and equivalent provider waits use this vocabulary; Run Ownership, recursive
batch quarantine, and Daytona lease cleanup retain their domain-specific state
and fallback policy.
_Avoid_: generic task scheduler, background job, Run Ownership replacement

**Daytona Admission**:
The process-wide bound on acquiring or active Daytona Interpreter Leases. A
wait uses the Turn Timeout deadline and never creates a second timeout policy.
_Avoid_: Sandbox retention limit, per-Session claim, RLM Option

**Root Model**:
The model role that steers the recursive trajectory for a Run (generates and
revises interpreter work toward a final answer). Its RLM action iterations do
not increase recursive delegation depth; the Root executor starts at depth 0.
_Avoid_: primary LLM, chat model alone, Sub Model

**Sub Model**:
The model role that answers sub-queries issued from interpreter work during a
Run (e.g. bounded `llm_query`-style calls), distinct from the Root Model role
even when both use the same provider configuration. It also handles the
bounded depth fallback when a child attempts delegation beyond native depth 1.
_Avoid_: Root Model, helper model (vague), utility model (optional third role)

**Delegation Metrics**:
Run-scoped, content-free measurements of Root/Sub LM calls by role and depth,
token totals, latency, recursive calls, child settlement, depth fallback, and
peak sibling concurrency. They are internal observability, benchmark, and trace
data rather than public Turn schema.
_Avoid_: billing record, hidden prompt capture, public answer quality

**Recursive Child / Recursive Batch**:
The Root may call `rlm_query` for one isolated iterative specialist or
`rlm_query_batched` for ordered independent specialists. Fleet computes each
reservation as `child_depth = executor_depth + 1`: Root depth 0 produces native
child depth 1 with a fresh Sandbox and copied DSPy LM runtimes. A child receives
an immutable committed Session History snapshot, bounded Session context, only
`rlm_query` plus native semantic tools; if it delegates, depth 2 is settled
by the bounded Sub-LM fallback rather than another native RLM or Sandbox. A
batch is Root-only, every item remains depth 1, shared call budget is reserved
atomically, concurrency is bounded, and settlement is all-or-nothing.
_Avoid_: Child Session, recursive swarm, shared LM history, configurable native
`max_depth`

**Code-Interpreter Context**:
Python REPL state for one resident Session runtime inside a Root Sandbox. Fleet
RLM reuses the caller-owned context across sequential clean Turns while the
runtime remains compatible, healthy, and resident. Every Turn still receives
fresh request/capability bindings and DSPy `REPLHistory`; failure, eviction,
restart, or Sandbox replacement may lose arbitrary globals. Durable continuity
is the committed Session History and Workspace Volume Scope.
_Avoid_: durable Memory, hidden trajectory as History, cross-Session REPL sharing

**Interpreter Lease**:
Temporary right to use a Code-Interpreter Context. A disposable child lease is
Turn-scoped; a Root Session lease may span sequential clean Turns. Release
ends the owned interpreter context and never destroys the durable Volume by
itself.
_Avoid_: Sandbox ownership, process handle alone, cross-Session REPL continuity

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

**Workspace Memory**:
Daytona-only workspace-wide immediate state at `memory/MEMORIES.md` under the
already workspace-scoped Volume mount (legacy root `MEMORIES.md` migrates on
first open; content is never lost). Records are canonical v1/v3 lines; new
appends are v3 (`- [ts] **Category** <!-- id:8hex -->: learning`) with fresh
ids. v1 rows derive a deterministic id from canonical text plus valid-record
occurrence when read, so duplicate legacy rows remain separately addressable;
duplicate persisted ids fail closed rather than selecting an arbitrary row.
Reads are tolerant (humans edit the file): malformed lines are skipped with a
bounded warning count while writes stay strictly validated.
`read_workspace_memory` loads the newest bounded complete records on demand;
`remember` (alias `update_workspace_memory`) appends one normalized record only
for an explicit user request and is idempotent for the same record;
`list_memories` pages id-addressed entries; `search_memories` deterministically
ranks older relevant entries by bounded lexical score instead of relying on
recency alone; `edit_memory` upgrades legacy rows to v3 or rewrites v3 while preserving
id and timestamp; `forget` removes exactly one entry. Edit and forget use one
mounted-agent read-modify-publish operation.
Each Turn's `session_context` also carries a bounded <= 4 KiB
`workspace_memory tail` digest so relevant and recent
learnings are visible without a Tool call. Records are durable independently
of Turn Commit and survive failed or cancelled Runs and Sandbox replacement.
Optional read-side degradation is classified once by
`workspace/memory.py` into a bounded sanitized category; mutation and list
operations remain strict. Daytona supplies the production storage adapter, but
Memory policy and diagnostics remain provider-neutral. Do not add a second
Memory warning taxonomy or leak raw provider, path, query, or record content
through diagnostics.
_Avoid_: Session History, unbounded learned state

**Workspace Volume Tree**:
The Daytona-only bounded read-only HTTP/TUI projection of relative paths from
the local Workspace Volume. It is not a file-content API, provider-path API,
mutation surface, or general-purpose Sandbox browser.
_Avoid_: Volume mount, Sandbox filesystem browser, public storage path

### Skills and files

**Session Workspace**:
Private durable working files owned by one Session within Workspace Volume
Scope. Successful writes, unique-fragment edits, and file-or-empty-directory
deletes persist immediately across Turns, failed or cancelled Runs, and Sandbox
replacement; they are not Session History, commit-gated Artifacts, or
Code-Interpreter Context. Edits/deletes never recurse or follow symlinks and
accept optional SHA-256 preconditions.
Replacement continuity applies to the mounted bytes only, never interpreter
globals or an Interpreter Lease.
_Avoid_: Sandbox Workspace, Artifact, Attachment, durable REPL variables

**Skill Card**:
Bounded discovery metadata for an authorized visible Skill. Every Card's name
and description is available to the primary RLM at Turn startup; instructions
and resource bodies are excluded.
_Avoid_: full Skill, prompt dump

**Skill**:
Agent Skills-compatible directory containing `SKILL.md` plus optional bounded
`scripts/`, `references/`, and `assets/`. The instruction body loads only when
invoked; each supporting resource loads only after a subsequent explicit read.
_Avoid_: Skill Card, automatic Memory recall

**RLM Execution Spec**:
Immutable host-composed inputs for one Turn: bounded Skill Cards, one validated
Signature, output schema identity, explicit host Tools and event views, and
runtime-specific Workspace metadata. Exact selections may preload up to four
pinned Skills; at most one selected Skill may provide the Signature.
_Avoid_: mutable registries, Markdown-defined tools, HTTP-provided Python

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
Fetching a Skill body on demand, then fetching individual declared resources
only as needed, via Host-Mediated Tools that re-authorize every call. An exact
explicit selection performs the body-load step during bounded preparation.
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

**Result Snapshot**:
Private commit-gated Daytona Volume derivative of one successful typed Turn
result. It is never the replay source, a public Artifact, or an API resource.
_Avoid_: CommittedTurn, Artifact, checkpoint, Code-Interpreter Context

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

## Module map

### Workspace namespace disambiguation

Workspace domain policy is provider-neutral; Daytona composition supplies the
production mounted-agent and ephemeral-Sandbox adapters. Keep these roles
distinct: Session Workspace files, the Workspace files API, Projects, Memory,
URL sources, Attachments, and provider transport are not interchangeable.

| Module | Role | Primary callers |
| --- | --- | --- |
| `paths.py` | Provider-neutral Volume layout, mount validation, and path identity primitives shared by provisioning and domains | standard library |
| `workspace/storage.py` | Bounded sync/async storage ports, root-bound agent sessions, opaque Memory storage, Volume adapters, and cleanup ports | Workspace and Memory domains; Daytona composition; artifacts |
| `workspace/workspace.py` | Session Workspace domain, `WorkspaceToolHost`, and the `/api/files` `WorkspaceFileService` | RLM capability preparation, API route, composition |
| `workspace/projects.py` | Explicit `projects/<slug>/` path policy and `ProjectToolHost` | RLM capability preparation and composition |
| `workspace/memory.py` | Memory parsing, IDs, search, mutation policy, diagnostics, candidate collection, promotion, and tool hosts | Turn preparation/lifecycle, composition, outbox |
| `workspace/url.py` | URL safety, bounded public fetching, source stores, and `UrlToolHost` | RLM capability preparation and composition |
| `attachments/` | Attachment models, authorization/lifecycle policy, blob/catalog adapters, and `AttachmentToolHost` | API, Turn preparation, composition |
| `artifacts/tools.py` | Commit-gated `ArtifactToolHost` candidate staging and Workspace publication bridge | Turn preparation and Daytona composition |
| `composition/daytona_workspace.py` | Daytona mounted Workspace Agent sessions, ephemeral Workspace gateways, and orphan-byte cleanup | Daytona composition and API dependencies |
| `daytona/workspace_agent/` | Host run/decode adapter and versioned handshake transport for the packaged stdlib-only `workspace_agent/runtime.py` `handle(request)` | `workspace/storage.py`, Daytona runtime |

### `rlm/` package one-liner

`rlm/` owns the native DSPy program/result/event seams, Root/Sub model roles,
Session-scoped RLM/interpreter reuse, delegation metrics, routing evaluation,
and fixed-depth recursive child executor. Root-only batch delegation uses
ordered, bounded sibling execution.

### `daytona/` package one-liners

- `__init__.py` — ownership package: side-effect-free; import adapter types from owning modules.
- `broker.py` — canonical source, HTTP transport, and sync bridge boundary for the in-sandbox host-tool broker.
- `diagnostics.py` — opt-in disposable provider/mount probes (`fleet doctor daytona`).
- `errors.py` — Fleet-facing error types and sanitized mapping for Daytona failures.
- `_cleanup.py` — private callback and late-cleanup invocation boundary.
- `_lease.py` — private cancellation-safe root/child lease state machine.
- `interpreter.py` — `DaytonaCodeInterpreter`: DSPy RLM interpreter adapter (execution, observation, SUBMIT mediation, sync bridge).
- `interpreter_output.py` — public per-step output projection: marker-hiding stdout replay, capped deltas, stream-closed tracking, final flush.
- `optimization_evaluator.py` — disposable no-volume lifecycle for the offline signature-optimization lane.
- `platform.py` — live SandboxPlatform and VolumeClient adapters over the Daytona SDK.
- `provisioning.py` — strict async Sandbox, Volume, mount, and layout provisioning.
- `recursive_child_runtime.py` — contracted single owner for native DSPy
  recursive children: acquisition with admission/authorization fences, the
  explicit single-owner joinable lease close state, strict interpreter/broker
  shutdown with confirmed provider deletion and absence, admission
  restoration, and late/quarantined cleanup ownership that survives
  owner-loop and dispatch loss.
- `session_manager.py` — interpreter lease acquire/release and Sandbox binding lifecycle.
- `workspace_agent/` — Workspace Agent host protocol/client package; the
  packaged `workspace_agent/runtime.py` owns the explicit stdlib-only
  `handle(request)` used by installed and fallback launchers.

The established `daytona/run_environment.py` entry point is a side-effect-free
compatibility export; the canonical run-environment inventory lives in
`composition/daytona_environment.py`. Mounted Workspace sessions and ephemeral
Workspace gateways live in `composition/daytona_workspace.py`. Daytona remains
the exclusive SDK and transport boundary, while domain policy stays under
`workspace/` and `attachments/`.

The post-P25 maintainability baseline keeps one canonical transformation at
each seam: `RunLifecycle.finish()` commits, `TurnRuntime` settles the
stream (`chat/turn_coordinator.py` retains the historical compatibility name),
`OwnedEffect` supplies the provider-neutral wait vocabulary, and the
TUI adapters feed one canonical reducer. See the [P34 maintainability freeze
guide](../../docs/how-to-guides/maintainability-freeze.md) for the certification
matrix and cleanup guardrails.

## Out of this context (for now)

These shared or live terms are **not** clean product claims until promoted:

- **Automatic or unbounded Memory** beyond the fixed Daytona Workspace Memory
  Tool contract
- **Child Session**
- **Execution Mode** client switch (`simple` vs `rlm`)
- **Managed Target**, GEPA selection, and promotion artifacts
- Warm multi-run **Code-Interpreter Context** (clean is per-Run only)
- Optional third model role beyond Root Model and Sub Model
- Shared-root **Volume** without Workspace Volume Scope as a multi-tenant claim
