# Fleet RLM clean-backend ticket plan

## Delivery rules

- Implement in dependency order.
- Each ticket must produce independently reviewable behavior.
- Use a failing test first, minimal implementation, passing tests, then commit.
- Do not create future packages before their first working feature.
- Default tests require no live model, Daytona account, or database.
- Live claims require explicit live lanes and evidence.

## Provisional compatibility baseline

```text
Python >=3.11,<3.14
dspy==3.3.0b1
daytona==0.192.0
fastapi[standard]==0.139.0
Pydantic v2
SQLAlchemy async 2.x
PostgreSQL
```

A dependency change requires contract tests for DSPy RLM construction, FastAPI SSE, and Daytona lifecycle/Volume behavior.

# Phase 1 - RLM/SSE/Daytona kernel

## K-001 Bootstrap and dependency contracts

Create the import-safe package, typed configuration, and framework contract tests.

**Files** (parallel package until cutover)

```text
src/fleet_rlm_clean/__init__.py
src/fleet_rlm_clean/app.py
src/fleet_rlm_clean/config.py
tests/unit/clean_backend/test_import_safety.py
tests/contracts/clean_backend/test_framework_contracts.py
```

**Acceptance**

- Package imports without credentials or network access.
- Required `dspy.RLM` constructor fields exist.
- FastAPI `EventSourceResponse` imports.
- Daytona adapter imports.
- Secrets are excluded from settings serialization.

## K-002 RuntimeEvent v1 and SSE projection

Create the immutable event envelope, event kinds, ordering rules, terminal rules, and SSE projector.

**Files** (parallel package until cutover)

```text
src/fleet_rlm_clean/rlm/events.py
src/fleet_rlm_clean/api/sse.py
tests/unit/clean_backend/test_runtime_events.py
tests/contracts/clean_backend/test_sse_projection.py
tests/contracts/clean_backend/fixtures/valid_run_transcript.sse
```

**Acceptance**

- Sequence is strictly increasing per run.
- Exactly one terminal event is allowed.
- Public schema contains no hidden reasoning field.
- A committed fixture represents a valid SSE transcript.

## K-003 Daytona interpreter adapter

Create a package-owned adapter for Daytona code execution.

**Files** (parallel package until cutover)

```text
src/fleet_rlm_clean/daytona/errors.py
src/fleet_rlm_clean/daytona/client.py
src/fleet_rlm_clean/daytona/interpreter.py
src/fleet_rlm_clean/daytona/leases.py
tests/unit/clean_backend/test_daytona_adapter.py
tests/contracts/clean_backend/test_daytona_import_boundary.py
tests/live/clean_backend/test_daytona_interpreter_state.py
```

**Acceptance**

- No other package imports Daytona SDK DTOs.
- Provider errors map to Fleet error types.
- Shutdown/release is idempotent.
- Opt-in live test proves Python state across active interpreter calls.

## K-004 Root/sub model bundle, budgets, and RLMFactory

Create `RLMModelBundle`, `RLMBudget`, typed signature, and the only factory allowed to call `dspy.RLM(...)`.

**Acceptance**

- Root and sub roles are distinct.
- `sub_lm`, interpreter, tools, `max_iters`, `max_llm_calls`, and `max_output_chars` are explicit.
- Invalid budgets fail before external execution.
- Each factory call returns a new RLM instance.

## K-005 RLMRunner

Execute one DSPy turn without FastAPI or persistence dependencies.

**Acceptance**

- Emits start, progress/text, usage, and one terminal event.
- Applies root LM through scoped DSPy context.
- Uses the configured smaller `sub_lm`.
- Converts raw failures into safe typed failures.
- Closes interpreter resources in `finally`.
- Concurrent runs do not share mutable RLM state.

## K-006 FastAPI chat endpoint

Wire the app factory, lifespan, dependencies, `TurnCoordinator`, and `POST /api/chat`.

**Acceptance**

- Route returns typed SSE.
- Route contains no DSPy construction or Daytona SDK call.
- Disconnect closes the upstream async generator.
- OpenAPI contains the intended request contract.

## K-007 Live kernel evidence

Prove a real FastAPI -> SSE -> `dspy.RLM` -> Daytona path.

**Required proof**

- real RLM;
- real Daytona interpreter;
- root model execution;
- at least one `llm_query` or `llm_query_batched` call through the smaller model;
- generated Python execution;
- safe terminal SSE transcript;
- exact commit and lockfile recorded.

# Phase 2 - Stateful session runtime

## S-001 Foundation persistence schema

Add tenants, users, workspaces, sessions, turns, runs, checkpoints, execution bindings, attachments, artifacts, and Skill records.

## S-002 Session repository and `dspy.History`

Persist completed turns and reconstruct History in stable order.

**Acceptance**

- Only committed turns enter History.
- A second turn receives the first completed exchange.
- FastAPI restart does not lose session state.

## S-003 Workspace Volume and safe layout

Resolve or create a workspace Volume, mount it at the configured path, and allocate validated session/run paths.

**Acceptance**

- Path traversal and untrusted identifiers are rejected.
- Every run receives a unique staging root.
- A replacement Sandbox can mount the same Volume and read durable content.

## S-004 DaytonaSessionManager

Implement acquire/release and capability-aware lifecycle behavior.

**Acceptance**

- Running Sandbox reuse works.
- Stop/start works.
- Pause/resume is used only when supported.
- Archive/restore is used only when supported.
- Missing or unhealthy Sandboxes are recreated with the expected Volume.
- Lease release never implies Sandbox deletion.

## S-005 Atomic checkpoints, idempotency, and locks

Add optimistic concurrency, immutable checkpoints, request idempotency, and a session mutation lock.

**Acceptance**

- Failed/cancelled runs do not advance the successful checkpoint.
- Duplicate idempotency keys do not execute twice.
- Stale checkpoint commits fail deterministically.

## S-006 Stateful live recovery

Prove two-turn History use, API restart recovery, inactive Sandbox lifecycle, and replacement-Sandbox recovery.

# Phase 3 - Progressive capabilities

## C-001 Attachment upload and staging

Upload separately, reference by opaque ID, reauthorize at staging/read time, and expose only Fleet-controlled Sandbox paths.

## C-002 Durable artifact store

Support Markdown, JSON, and text artifacts on the mounted Volume with transactional metadata and checksums.

## C-003 Skill registry, visibility, and SkillCards

Implement deterministic authorization and bounded metadata cards. Optional utility-model ranking may only rank already-authorized candidates.

## C-004 Progressive Skill tools

Bind host-mediated `load_skill` and `read_skill_resource` operations.

**Acceptance**

- Full Skill instructions are absent at discovery time.
- Every load/resource call rechecks scope, trust, and version.
- Resource paths are Skill-relative and normalized.

## C-005 Live capability flow

One live turn must load a Skill, read an attachment by ID, create a durable artifact, stream the operations, and retrieve the artifact after Sandbox replacement.

# Phase 4 - Foundation hardening

## H-001 Authentication and workspace isolation

Protect sessions, bindings, Skills, attachments, artifacts, and Volume references.

## H-002 Idempotency and concurrency hardening

Prevent duplicate turns, stale writes, active-lease races, and canonical Volume write races.

## H-003 Cancellation, timeout, and budget enforcement

Client disconnect, authenticated cancel, timeout, and budget exhaustion converge on one terminal state and preserve the prior checkpoint.

## H-004 Redaction and public errors

Prevent credentials, DSNs, raw provider errors, private paths, and internal prompts from entering SSE or public records. Preserve safe assistant text.

## H-005 Runtime observability

Record root/sub usage, iterations, subqueries, tools, Skills, attachments, artifacts, Sandbox/interpreter/Volume references, duration, cost, and terminal state.

## H-006 Foundation promotion gate

Run static, unit, contract, integration, and live end-to-end gates. Close the foundation only when every acceptance item in `to-spec.md` passes on the exact commit.

# Phase 5 - Long-context memory

## M-001 Scoped MemoryItem and gateway

Add session, user, workspace, agent, tenant, and system scopes with version, provenance, importance, sensitivity, and governed content reference.

## M-002 SessionContextBuilder

Combine recent History, accepted summary, relevant memories, artifacts, and open-task state within a context budget.

## M-003 Memory proposals and commits

The RLM proposes; Fleet validates, checks base version and evidence, then commits or rejects.

## M-004 Event-driven consolidation

Implement idempotent consolidation with a high-water mark, source references, contradiction handling, correction, and supersession.

## M-005 Long-context evidence

Prove scoped retrieval across sessions, token-budget compliance, provenance, and Sandbox-replacement survival.

# Phase 6 - Self-improvement

## I-001 Skill candidate lifecycle

Create candidate, validation, evaluation, approval, activation, and rollback states. Generated candidates begin untrusted and non-executable.

## I-002 Immutable evaluation datasets

Separate training, selection, and sealed promotion-test partitions.

## I-003 GEPA execution

Optimize registered DSPy or Skill-instruction targets with explicit task/reflection models, metrics, budgets, checkpoints, and artifact round-trip verification.

## I-004 Promotion, activation, and rollback

Require sealed-test improvement, hard-gate pass, cost/latency evidence, explicit approval, workspace-scoped activation, and rollback pointer.

## I-005 Full-feature evidence

Prove that memory adaptation, Skill evolution, and GEPA remain distinct, evidence-backed, and outside normal chat execution.
