# Fleet RLM clean-backend specification

## 1. Objective

Fleet RLM is a stateful recursive-agent backend consumed by a chat UI. Its canonical request path is FastAPI HTTP plus Server-Sent Events. Its canonical open-ended execution engine is `dspy.RLM`. Generated Python executes inside a Daytona Sandbox, and durable filesystem-native state lives on a Daytona Volume mounted into that Sandbox.

The foundation must deliver one complete vertical product path before long-term memory automation, generated Skills, multi-agent orchestration, or GEPA optimization are added.

## 2. Product contract

```text
Chat UI
  -> authenticated FastAPI request
  -> POST /api/chat
  -> typed SSE RuntimeEvents
  -> stateful RLM turn
  -> Daytona execution
  -> transactional checkpoint
  -> later session continuation
```

The frontend understands sessions, messages, attachments, artifacts, and public RuntimeEvents. It does not understand DSPy internals, model-provider names, Sandbox IDs, Volume paths, interpreter contexts, or Skill trust internals.

## 3. Foundation scope

The foundation includes:

- FastAPI application and lifespan;
- SSE chat streaming;
- session creation and continuation;
- persisted `dspy.History`;
- a fresh `dspy.RLM` for every concurrent turn;
- a capable root LM and a smaller `sub_lm`;
- Daytona Sandbox execution and mounted Volume storage;
- Sandbox acquisition, start/stop or pause/resume where supported, archive/restore where supported, and replacement recovery;
- read-only progressive Skills;
- attachment upload and authorized staging;
- durable text, Markdown, and JSON artifacts;
- finite budgets, cancellation, idempotency, authorization, redaction, observability, and evidence.

The foundation does not include automatic memory consolidation, generated production Skills, a Skill marketplace, broad child-agent trees, online optimization, or a rich trace administration UI.

## 4. Public API

### 4.1 Sessions

```text
POST   /api/sessions
GET    /api/sessions
GET    /api/sessions/{session_id}
GET    /api/sessions/{session_id}/turns
POST   /api/sessions/{session_id}/archive
```

Every session belongs to one user and one workspace. Cross-workspace access is rejected before any runtime resource is acquired.

### 4.2 Chat

```text
POST /api/chat
```

Request fields:

```json
{
  "session_id": "uuid",
  "message": "string",
  "attachment_ids": ["uuid"]
}
```

Rules:

- `session_id` may be omitted only when the route is explicitly configured to create a new session.
- `message` is required and bounded.
- attachment IDs are authorized before the SSE stream begins.
- the client cannot select root model, sub-model, provider, RLM budgets, Sandbox, Volume, Skill trust, or execution backend.
- idempotency is supplied through a request header and scoped to user plus session.

### 4.3 Cancellation

```text
POST /api/runs/{run_id}/cancel
```

Cancellation is authenticated and idempotent. A cancelled run emits exactly one terminal event and does not append an incomplete assistant message to `dspy.History`.

### 4.4 Files and artifacts

```text
POST /api/files
GET  /api/files/{file_id}
GET  /api/artifacts/{artifact_id}
```

Uploads and artifacts are referenced by logical IDs. Public contracts never expose host paths or unrestricted Volume paths.

## 5. SSE contract

The server emits versioned RuntimeEvents with this envelope:

```json
{
  "schema_version": 1,
  "event_id": "uuid",
  "run_id": "uuid",
  "session_id": "uuid",
  "sequence": 1,
  "timestamp": "RFC3339",
  "kind": "run.started",
  "payload": {}
}
```

Foundation event kinds:

```text
run.started
status
text.delta
text.completed
tool.started
tool.completed
skill.loaded
attachment.read
artifact.created
usage
warning
error
run.completed
```

Requirements:

- sequence numbers are strictly increasing per run;
- keepalive comments do not consume sequence numbers;
- exactly one terminal `error` or `run.completed` event is emitted;
- all upstream iterators and leases close on disconnect;
- public events contain concise progress, not hidden chain-of-thought;
- raw provider exceptions, credentials, environment values, private paths, and internal prompts are prohibited;
- terminal events include status, duration, usage, artifact IDs, checkpoint version, and degraded state.

## 6. Turn lifecycle

A normal turn follows this order:

1. Authenticate user and resolve workspace.
2. Validate the request and authorize session and attachments.
3. Claim the session execution lock and idempotency key.
4. Create a run record.
5. Load the latest successful session checkpoint.
6. Reconstruct `dspy.History` and the rolling session summary.
7. Resolve the server-owned model bundle.
8. Resolve authorized SkillCards.
9. Acquire an `InterpreterLease` from `DaytonaSessionManager`.
10. Build `RLMTurnContext`.
11. Create a fresh `dspy.RLM` through `RLMFactory`.
12. Execute the turn off the FastAPI event loop.
13. Normalize the trajectory into public RuntimeEvents and private trace records.
14. Persist the completed assistant turn, usage, artifacts, and new checkpoint in one logical transaction.
15. Release the interpreter lease and session lock.

A failed or cancelled turn is recorded as a run but does not advance the successful conversation checkpoint.

## 7. DSPy RLM behavior

### 7.1 Runtime identity

The application-level class is `RLMRunner`. There is no `DirectRLMRunner`, `RLMAgent`, generic agent-runtime selector, or second agent engine.

### 7.2 Instance lifecycle

- every concurrent turn receives a fresh `dspy.RLM` instance;
- an RLM instance is never shared across sessions or simultaneous runs;
- the interpreter is supplied through an acquired lease;
- the RLM signature is typed and stable;
- all RLM constructor parameters are passed explicitly and protected by framework contract tests.

### 7.3 Model roles

`RLMModelBundle` contains:

```text
root_lm      required
sub_lm       required
utility_lm   optional after the kernel
```

The root LM controls planning, generated Python, Skill activation decisions, observation evaluation, and final synthesis.

The sub-LM is invoked only through `llm_query` and `llm_query_batched` for bounded semantic work. It receives only the context selected for that subquery.

Model roles are configured by server-side capability profiles rather than provider names in public APIs.

### 7.4 Foundation budgets

Defaults are configurable but finite:

```text
max_iters                20
max_llm_calls            50
max_output_chars         10,000
max_wall_seconds         300
max_sub_lm_concurrency   8
max_tool_calls           32
max_skill_loads          8
max_artifact_bytes       10 MiB per artifact
```

A budget ledger records consumption by category. Exhaustion produces a stable terminal status and never silently falls back to an unbounded path.

## 8. Stateful sessions and long context

### 8.1 Foundation state

The durable session record includes:

- session, user, and workspace identity;
- ordered completed turns;
- reconstructable `dspy.History`;
- rolling session summary;
- attachment and artifact references;
- latest successful checkpoint;
- Daytona Sandbox and Volume binding metadata;
- model, Skill, and runtime provenance.

`dspy.History` remains the conversational state supplied to the RLM. The database is the authoritative ordered record used to reconstruct it.

### 8.2 Context composition

A turn receives:

```text
current request
+ recent dspy.History
+ accepted rolling summary
+ authorized SkillCards
+ attachment metadata
+ artifact references
```

The foundation does not inject every prior turn or every durable file into the root prompt.

### 8.3 Later memory layer

Phase 5 adds scoped episodic and semantic memory, retrieval, proposals, consolidation, contradiction handling, and expiration. Durable memory remains distinct from raw chat history and raw trajectories.

## 9. Daytona execution and storage

### 9.1 Sandbox ownership

Fleet maintains one logical root Sandbox binding per active session. The Sandbox may be running, stopped, paused when supported, archived when supported, replaced, or deleted after retention expiry.

`RLMRunner` does not own Sandbox deletion. `DaytonaSessionManager` owns lifecycle policy.

### 9.2 Interpreter state

Python variables, imports, and functions may persist across active interpreter calls. Fleet treats them as a performance optimization. Session correctness never depends on their survival across lifecycle transitions.

### 9.3 Volume ownership

A workspace-scoped Daytona Volume is mounted into the Sandbox at:

```text
/home/daytona/fleet
```

The authorized mounted view contains:

```text
/home/daytona/fleet/
├── skills/
├── memory/
├── artifacts/
├── attachments/
└── sessions/{session_id}/runs/{run_id}/
```

The database owns identity, permissions, versions, checksums, provenance, checkpoints, and audit state. The mounted Volume owns durable file bodies and large filesystem-native content.

Volumes are not used as a transaction manager. Concurrent writers use unique run or proposal paths, followed by database-coordinated logical promotion.

### 9.4 Recovery

Recovery attempts, in order:

1. reuse a running Sandbox and active interpreter context;
2. start or resume an existing Sandbox and recreate interpreter context as needed;
3. restore an archived Sandbox when supported;
4. create a replacement Sandbox, mount the existing Volume, and reconstruct from the latest successful checkpoint.

## 10. Skills

### 10.1 Foundation model

The RLM initially receives compact authorized SkillCards containing:

```text
id
name
description
scope
version
trust level
affordances
resource availability
```

The full `SKILL.md` is not injected at run start.

Foundation host tools:

```text
load_skill(skill_id)
read_skill_resource(skill_id, resource_id)
```

Authorization is deterministic and occurs on the host. The model cannot make an invisible Skill visible by inventing an ID or path.

### 10.2 Deferred Skill features

Trusted script execution, user-created Skills, session-generated Skills, patch proposals, and promotion are deferred until progressive read-only loading is proven.

## 11. Attachments and artifacts

Attachments:

- are uploaded outside the chat request;
- are authorized by user and workspace;
- enter chat by ID and metadata;
- are staged into Fleet-controlled Sandbox paths;
- never reveal host paths.

Foundation artifacts:

- support text, Markdown, and JSON;
- are written to unique run-scoped Volume paths;
- have database metadata, checksum, media type, size, and provenance;
- emit `artifact.created` events;
- remain retrievable after Sandbox replacement.

## 12. Security and authority

Deterministic Python owns:

- authentication and workspace authorization;
- session ownership;
- Skill visibility and trust;
- file and artifact path safety;
- RLM budgets and cancellation;
- model-role routing;
- persistence and optimistic concurrency;
- redaction and public error mapping.

Generated code cannot access model-provider credentials, Fleet database credentials, the Daytona control-plane token, or unrestricted host tools.

## 13. Observability

Every run records:

- session and run identity;
- root and sub-model profiles;
- RLM iteration count;
- sub-LM calls and concurrency;
- tool calls;
- Skills loaded;
- attachments read;
- artifacts created;
- Sandbox, interpreter, and Volume references;
- tokens, duration, estimated cost, and terminal status;
- cancellation, timeout, budget exhaustion, degraded behavior, and fallback.

External exporters such as MLflow are optional adapters. Internal structured traces and run persistence are mandatory.

## 14. Foundation acceptance scenario

One automated integration test and one opt-in live test must prove:

1. A user starts a session from the Chat UI boundary.
2. `POST /api/chat` returns SSE.
3. `RLMRunner` constructs a real fresh `dspy.RLM`.
4. The root LM generates Python that executes in Daytona.
5. Generated code invokes the smaller sub-LM.
6. The RLM loads one authorized Skill and reads one Skill resource.
7. The RLM reads one uploaded attachment by ID.
8. The RLM creates one durable artifact on the mounted Volume.
9. The response, usage, and artifact events stream in order.
10. The turn and checkpoint commit successfully.
11. FastAPI is restarted.
12. The Sandbox is stopped, resumed, archived, or replaced according to the tested lane.
13. A second turn restores `dspy.History` and existing artifact references.
14. The second answer demonstrably uses prior session context.
15. No secret, private path, raw exception, or hidden reasoning appears in public events.

## 15. Later capabilities

### Phase 5: long-context memory

- scoped user, workspace, session, agent, and tenant memories;
- semantic and episodic retrieval;
- evidence-linked memory proposals;
- versioned commits and supersession;
- background consolidation;
- contradiction and expiration policy.

### Phase 6: self-improvement

- memory consolidation from accepted histories and trajectories;
- Skill patch and new-Skill proposals;
- review, evaluation, approval, activation, and rollback;
- offline GEPA against immutable datasets and a sealed promotion-test partition;
- no optimizer or automatic mutation in normal chat execution.