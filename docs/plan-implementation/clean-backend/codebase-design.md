# Fleet RLM clean-backend codebase design

## 1. Design goal

The source tree must make the product kernel obvious:

```text
FastAPI SSE
  -> TurnCoordinator
  -> RLMRunner
  -> dspy.RLM
  -> DaytonaInterpreter
  -> Daytona Sandbox + mounted Volume
```

The design favors explicit domain names, narrow modules, dependency inversion at external boundaries, and vertical behavior over speculative platform abstractions.

## 2. Foundation source tree

```text
src/fleet_rlm/
├── __init__.py
├── app.py
├── config.py
│
├── api/
│   ├── __init__.py
│   ├── dependencies.py
│   ├── errors.py
│   ├── schemas.py
│   ├── sse.py
│   └── routes/
│       ├── chat.py
│       ├── sessions.py
│       ├── files.py
│       ├── artifacts.py
│       └── health.py
│
├── chat/
│   ├── __init__.py
│   ├── commands.py
│   └── turn_coordinator.py
│
├── rlm/
│   ├── __init__.py
│   ├── runner.py
│   ├── factory.py
│   ├── signature.py
│   ├── context.py
│   ├── events.py
│   ├── budgets.py
│   ├── model_bundle.py
│   ├── tools.py
│   ├── trajectory.py
│   └── errors.py
│
├── daytona/
│   ├── __init__.py
│   ├── client.py
│   ├── interpreter.py
│   ├── session_manager.py
│   ├── leases.py
│   ├── lifecycle.py
│   ├── volumes.py
│   ├── paths.py
│   └── errors.py
│
├── sessions/
│   ├── __init__.py
│   ├── models.py
│   ├── repository.py
│   ├── history.py
│   ├── checkpoints.py
│   └── errors.py
│
├── skills/
│   ├── __init__.py
│   ├── models.py
│   ├── registry.py
│   ├── visibility.py
│   ├── selection.py
│   ├── loader.py
│   └── errors.py
│
├── files/
│   ├── __init__.py
│   ├── models.py
│   ├── repository.py
│   ├── uploads.py
│   ├── staging.py
│   └── safety.py
│
├── artifacts/
│   ├── __init__.py
│   ├── models.py
│   ├── repository.py
│   ├── store.py
│   ├── paths.py
│   └── checksums.py
│
├── persistence/
│   ├── __init__.py
│   ├── database.py
│   ├── models.py
│   ├── repositories.py
│   └── migrations/
│
└── observability/
    ├── __init__.py
    ├── recorder.py
    ├── redaction.py
    ├── usage.py
    └── logging.py
```

Do not create `memory/` or `quality/` packages during the foundation unless their first working feature is implemented in the same change. Phase 5 introduces `memory/`; Phase 6 introduces `quality/`.

Avoid generic packages named `utils`, `helpers`, `common`, `services`, or `managers` unless the name describes one concrete domain responsibility.

## 3. Module ownership

### `api/`

Owns FastAPI construction, lifespan, dependency injection, HTTP schemas, authentication dependencies, public error mapping, routes, and SSE projection.

It does not construct DSPy modules or call Daytona SDK objects directly.

### `chat/`

Owns the application use case for one chat turn.

`TurnCoordinator`:

- validates the command against the loaded session;
- claims idempotency and the session execution lock;
- creates the run record;
- reconstructs state;
- resolves authorized SkillCards and attachments;
- acquires an interpreter lease;
- calls `RLMRunner`;
- persists the terminal result and checkpoint;
- releases the lease and lock.

### `rlm/`

Owns one recursive DSPy execution.

`RLMRunner`:

- accepts a complete `RLMTurnContext`;
- creates a fresh `dspy.RLM` through `RLMFactory`;
- binds root and sub-model roles;
- binds the Daytona interpreter and approved host tools;
- enforces the budget ledger;
- executes the RLM off the FastAPI event loop;
- converts the trajectory into private trace records and public RuntimeEvents;
- returns a terminal `RLMTurnResult`.

`rlm/` has no FastAPI imports and no concrete SQLAlchemy repository imports.

### `daytona/`

Owns every Daytona SDK interaction:

- client construction;
- Sandbox create, start, stop, pause/resume when supported, archive/restore when supported, replacement, and delete;
- Volume resolution and mounting;
- interpreter creation;
- lease acquisition and release;
- provider-error normalization;
- approved path construction.

Higher packages depend on Fleet protocols and types, never Daytona SDK DTOs.

### `sessions/`

Owns durable conversation state:

- sessions;
- ordered completed turns;
- `dspy.History` reconstruction;
- rolling summary references;
- successful checkpoints;
- Sandbox and Volume binding metadata.

A failed or cancelled run does not advance the successful conversation checkpoint.

### `skills/`

Owns SkillCards, visibility, deterministic authorization, candidate selection, bundle loading, and resource access.

Foundation Skills are read-only. Script execution and mutation are later capabilities.

### `files/`

Owns uploaded attachment metadata, authorization, content storage references, safe Sandbox staging, and cleanup.

### `artifacts/`

Owns durable generated outputs, run-scoped paths, metadata, checksums, retrieval, and logical promotion.

### `persistence/`

Owns SQLAlchemy models, Postgres engine and transactions, migrations, and repository implementations. It implements domain protocols but does not define runtime policy.

### `observability/`

Owns structured run recording, usage accounting, redaction, safe logging, and optional external exporters. External exporter failure never corrupts the chat transaction.

## 4. Dependency direction

Allowed flow:

```text
api -> chat
chat -> sessions protocols
chat -> rlm
chat -> skills protocols
chat -> files protocols
chat -> artifacts protocols
chat -> daytona session protocol
chat -> observability protocol

rlm -> DSPy
rlm -> Daytona interpreter protocol
rlm -> model bundle
rlm -> approved tool callables

persistence -> SQLAlchemy/Postgres

daytona -> Daytona SDK
```

Forbidden imports:

```text
rlm -> FastAPI
rlm -> SQLAlchemy repositories
sessions -> Daytona SDK
skills -> FastAPI
persistence -> FastAPI
observability -> public route implementations
daytona -> session business rules
```

## 5. Core types and interfaces

### Chat command

```python
@dataclass(frozen=True)
class ChatTurnCommand:
    user_id: UUID
    workspace_id: UUID
    session_id: UUID
    message: str
    attachment_ids: tuple[UUID, ...]
    idempotency_key: str
```

### Runtime event

```python
@dataclass(frozen=True)
class RuntimeEvent:
    schema_version: Literal[1]
    event_id: UUID
    run_id: UUID
    session_id: UUID
    sequence: int
    timestamp: datetime
    kind: RuntimeEventKind
    payload: Mapping[str, JsonValue]
```

Runtime events are immutable. The recorder assigns sequence numbers; individual tools do not.

### RLM model bundle

```python
@dataclass(frozen=True)
class RLMModelBundle:
    root_lm: dspy.LM
    sub_lm: dspy.LM
    utility_lm: dspy.LM | None = None
```

### RLM budget

```python
@dataclass(frozen=True)
class RLMBudget:
    max_iters: int = 20
    max_llm_calls: int = 50
    max_output_chars: int = 10_000
    max_wall_seconds: int = 300
    max_sub_lm_concurrency: int = 8
    max_tool_calls: int = 32
    max_skill_loads: int = 8
    max_artifact_bytes: int = 10 * 1024 * 1024
```

### Interpreter lease

```python
@dataclass
class InterpreterLease:
    sandbox_id: str
    interpreter_id: str
    volume_id: str
    mount_path: PurePosixPath
    interpreter: CodeInterpreter
    async def release(self) -> None: ...
```

Lease release is idempotent and does not imply Sandbox deletion.

### RLM turn context

```python
@dataclass(frozen=True)
class RLMTurnContext:
    run_id: UUID
    session_id: UUID
    user_id: UUID
    workspace_id: UUID
    request: str
    history: dspy.History
    session_summary: str
    skill_cards: tuple[SkillCard, ...]
    attachments: tuple[AttachmentRef, ...]
    artifacts: tuple[ArtifactRef, ...]
    models: RLMModelBundle
    budget: RLMBudget
    lease: InterpreterLease
```

### RLM result

```python
@dataclass(frozen=True)
class RLMTurnResult:
    status: Literal["completed", "cancelled", "failed", "timed_out", "budget_exhausted", "degraded"]
    assistant_text: str | None
    usage: UsageSummary
    artifact_refs: tuple[ArtifactRef, ...]
    trace_ref: str
    checkpoint_delta: CheckpointDelta | None
```

### RLM runner

```python
class RLMRunner:
    async def stream(
        self,
        context: RLMTurnContext,
    ) -> AsyncIterator[RuntimeEvent]: ...
```

The terminal RuntimeEvent carries the serializable result summary. The private recorder stores the full `RLMTurnResult` for `TurnCoordinator`.

### Session repository

```python
class SessionRepository(Protocol):
    async def create(self, user_id: UUID, workspace_id: UUID) -> Session: ...
    async def load(self, session_id: UUID) -> SessionSnapshot: ...
    async def begin_run(self, command: ChatTurnCommand) -> RunRecord: ...
    async def commit_completed_turn(self, commit: CompletedTurnCommit) -> SessionCheckpoint: ...
    async def finish_failed_run(self, failure: FailedRunRecord) -> None: ...
```

### Daytona session manager

```python
class DaytonaSessionManager(Protocol):
    async def acquire(self, request: LeaseRequest) -> InterpreterLease: ...
    async def release(self, lease: InterpreterLease) -> None: ...
    async def stop(self, sandbox_id: str) -> None: ...
    async def pause(self, sandbox_id: str) -> None: ...
    async def archive(self, sandbox_id: str) -> None: ...
    async def replace(self, binding: SandboxBinding) -> SandboxBinding: ...
```

Unsupported provider lifecycle operations return a typed capability error; callers select the configured fallback lane.

## 6. Turn data flow

```text
POST /api/chat
  -> authenticate
  -> parse ChatTurnCommand
  -> TurnCoordinator.stream(command)
      -> claim idempotency and session lock
      -> load SessionSnapshot
      -> build dspy.History
      -> authorize attachments
      -> select authorized SkillCards
      -> resolve RLMModelBundle
      -> acquire InterpreterLease
      -> build RLMTurnContext
      -> RLMRunner.stream(context)
          -> RLMFactory.create(context)
          -> dspy.RLM(..., sub_lm=..., interpreter=...)
          -> generated Python runs in Daytona
          -> host tools enforce authority
          -> RuntimeEvents emitted
      -> commit terminal state
      -> release lease and session lock
  -> SSEProjector
```

The HTTP route contains no model, persistence, or Daytona orchestration logic.

## 7. DSPy integration

`RLMFactory` is the only module that calls `dspy.RLM(...)`.

It must:

- validate the installed constructor contract at test time;
- create a new module per turn;
- apply the root LM in the correct scoped DSPy context;
- pass the configured `sub_lm` explicitly;
- pass finite `max_iters`, `max_llm_calls`, and `max_output_chars` values;
- pass only approved tools;
- pass the acquired Daytona interpreter;
- use a typed Fleet signature;
- avoid silently filtering unsupported parameters.

`RLMRunner` executes blocking DSPy work in an executor or supported async path so the FastAPI event loop remains responsive.

## 8. Daytona design

### Sandbox binding

A session checkpoint stores logical binding metadata:

```text
sandbox_id
volume_id
mount_path
provider_state
last_verified_at
```

The binding is not exposed to clients.

### Volume layout

Fleet mounts the authorized workspace view at `/home/daytona/fleet`:

```text
/home/daytona/fleet/
├── skills/
├── memory/
├── artifacts/
├── attachments/
└── sessions/{session_id}/
    ├── runs/{run_id}/
    ├── staging/
    └── exports/
```

Generated code receives logical tools and approved paths. It does not discover tenant identifiers or other workspace roots.

### Lifecycle recovery

`acquire()` performs:

```text
running -> verify -> lease
stopped -> start -> recreate interpreter if needed -> lease
paused -> resume -> verify interpreter -> lease
archived -> restore -> recreate interpreter -> lease
missing/unhealthy -> replacement Sandbox -> mount Volume -> reconstruct -> lease
```

Durable correctness never depends on interpreter globals.

## 9. Persistence and transaction boundaries

Initial Postgres tables:

```text
users
workspaces
sessions
turns
runs
session_checkpoints
sandbox_bindings
attachments
artifacts
skills
```

One logical completed-turn transaction writes:

- assistant turn;
- usage summary;
- artifact metadata;
- terminal run state;
- next session checkpoint version.

Volume content is written to a unique staging path before the database commit. The commit records checksum and final logical URI. Cleanup removes abandoned staging data asynchronously.

## 10. Host tools

The foundation uses explicit tools rather than a speculative generic capability framework:

```text
load_skill
read_skill_resource
read_attachment
create_artifact
```

Each tool receives an immutable execution authority containing user, workspace, session, run, allowed roots, and budget ledger. Tool results are sanitized before returning to interpreter code or public events.

## 11. Test layout

```text
tests/
├── unit/
│   ├── api/
│   ├── rlm/
│   ├── daytona/
│   ├── sessions/
│   ├── skills/
│   ├── files/
│   ├── artifacts/
│   └── observability/
├── contracts/
│   ├── test_dspy_contract.py
│   ├── test_daytona_contract.py
│   ├── test_sse_contract.py
│   └── test_openapi_contract.py
├── integration/
│   ├── test_chat_turn.py
│   ├── test_session_resume.py
│   ├── test_skill_loading.py
│   └── test_artifact_recovery.py
└── live/
    ├── test_rlm_daytona_smoke.py
    ├── test_daytona_lifecycle.py
    └── test_foundation_acceptance.py
```

Default tests use deterministic fakes and require no live model, Daytona account, or Postgres server. Live tests are explicit and produce evidence artifacts.

## 12. Later source additions

Phase 5 adds:

```text
memory/
├── models.py
├── repository.py
├── retrieval.py
├── proposals.py
└── consolidation.py
```

Phase 6 adds:

```text
quality/
├── datasets.py
├── evaluation.py
├── optimization.py
├── promotion.py
├── activation.py
└── rollback.py
```

These packages are not scaffolded before their first working behavior is delivered.