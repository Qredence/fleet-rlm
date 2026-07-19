# ADR 0001: Coordinated Session-first Turn contract

- Status: Implemented
- Date: 2026-07-13
- Owners: Fleet RLM backend

## Implementation update (2026-07-18)

The coordinated Session-first lifecycle, closed Runtime Events, commit-gated
Artifacts, generated client contract, and single canonical baseline are current.
Later implementation decisions refine several original clauses:

- Turn input now includes up to four exact version-pinned Skill selections in
  addition to text and ordered unique Attachment ids.
- The API uses deterministic process-local User/Workspace scope and accepts no
  Authorization or synthetic identity headers; the original bearer-auth client
  clause is superseded.
- Fleet now uses stock native `dspy.RLM` behind the pinned contract seam without
  the private RLM subclass/iteration overrides described below.
- ADR 0002 replaces the environment set with `deno` and `daytona`; ADR 0003
  replaces the terminal renderer with pi-tui/native scrollback.
- `create_app()` seeds the static bundled Skill catalog before lifespan; runtime
  repositories, engines, LMs, and providers remain lifespan-owned.

The text below is retained as the accepted cutover decision and historical
context. Current behavior is specified by the architecture and reference guides.

## Context

The current backend exposes a top-level `/api/chat` action while its real unit
of work is a stateful Turn within an existing Session. Turn claim, context
construction, provider preparation, execution, durable detail, Artifact
promotion, commit, replay, terminal projection, and cleanup are split across
routes, a coordinator, repositories, and mutable live resources. Attachment
upload/staging and Artifact persistence similarly expose stores rather than one
caller-first lifecycle. The maintained terminal client compensates with loose
handwritten HTTP types and deliberately drops custom live data parts.

The system is still early enough to start from an empty database and replace
its canonical baseline. Preserving the current public and persistence shapes
would add compatibility machinery without preserving data that the final
contract requires, notably ordered Attachment ids bound into idempotent Turn
input.

## Decision

Fleet RLM will make one coordinated clean-break cutover. There is no dual serve,
dual write, compatibility alias, fallback adapter, deprecation facade, or
in-place data migration.

### Product resource contract

- Create a Turn with `POST /api/sessions/{session_id}/turns` and a required
  Session-scoped `Idempotency-Key`.
- The request is versioned text plus ordered, unique Attachment ids. Exact Skill
  selections were added later as noted above.
- Canonical Attachment endpoints live under `/api/attachments`; `/api/files`
  and generic File language are removed.
- Committed Artifact metadata remains read-only and gains an authorized,
  integrity-checked content endpoint.
- Cancellation is the idempotent resource
  `PUT /api/runs/{run_id}/cancellation`.
- Session `DELETE` is removed; `PATCH` owns archive state.
- Touched JSON failures use a closed safe `{code,message}` contract. Missing and
  foreign tenant-owned resources are indistinguishable.

### Turn ownership

One `TurnLifecycle` owns atomic begin/replay, input-bound idempotency, Run claim
and liveness, cancellation intent, Artifact publication plus Turn Commit,
failed finalization, and stale-claim recovery behind private state adapters.

One `TurnPreparationModule` owns ordered validation, capability selection,
environment acquisition, authoritative Attachment revalidation and staging,
tool/context construction, and reverse-order rollback. It returns an immutable
ready-to-run context and an explicit resource handle. Selection occurs before
provider acquisition; replay performs no preparation.

`TurnCoordinator.open()` completes lifecycle begin and preparation before HTTP
stream headers. Its returned stream owns execution, detail accumulation,
finish/failure settlement, terminal projection, and shielded idempotent cleanup.
Commit occurs before the committed semantic suffix. `RLMRunner` emits only
non-terminal observations and an outcome; it does not own persistence,
cancellation authorization, terminal events, or resource release.

### Durable and projected result

The assistant Turn stores exactly one versioned, closed `CommittedTurn`
aggregate. The user Turn stores exactly one versioned Turn input. Result text,
status, structured output, arbitrary part arrays, and Run result mirrors are
removed.

Runtime Events are a closed discriminated union. One exhaustive policy builds
the committed aggregate; exhaustive projectors derive live/replay Runtime
Events, AI SDK UI 7 v1 SSE chunks, and reload UIMessage parts. Lifecycle
terminal state stays outside durable semantic details. Unknown variants fail
rather than drop or fall back.

### Attachment and Artifact ownership

One `AttachmentLifecycle` owns upload, batch metadata, authorization, integrity,
and Run staging through aligned durable and hermetic adapters. Attachment bytes
remain private inputs and have no public download route.

Artifact Candidates remain private Run outputs. `TurnLifecycle.finish()`
publishes their verified bytes and metadata atomically with Turn Commit. One
`ArtifactReader` owns authorized committed metadata/content reads and verifies
length and SHA-256 before response.

### DSPy boundary

DSPy remains pinned to `3.3.0b1` behind local compatibility adapters in
`rlm/`. Fleet uses public `dspy.RLM` construction and `acall`, `dspy.context`,
typed Signatures, Tools/callables, `SandboxSerializable`, and stock `dspy.LM`.

`rlm.dspy_contract` owns version assertion, native construction, Prediction
extraction, trajectory normalization, and usage. `rlm.dspy_interpreter_contract`
owns the pinned inject extras beyond public `CodeInterpreter` (`output_fields`,
`_tools_registered` reinjection semantics, and `FinalOutput`). Concrete
interpreters remain under `fleet_rlm.daytona`; they call the interpreter
contract helpers rather than importing `dspy.primitives` directly. Fleet does
not fork RLM, subclass `BaseLM`, or call LiteLLM directly.

### Environments, persistence, and generated contracts

Composition selects exactly one explicit
`FLEET_RUN_ENVIRONMENT=hermetic|daytona`. Hermetic adapters are a real selected
environment, never a provider fallback. Daytona SDK imports remain confined to
`fleet_rlm.daytona`.

> **Superseded for the environment set by ADR 0002 and P1:** the canonical
> public set is now `deno` and `daytona`; deterministic fakes are private test
> composition. The explicit-selection, no-fallback, and Daytona
> import-boundary clauses above remain in force.

Alembic owns one fresh baseline with `down_revision=None`. It stores canonical
Turn input, one committed aggregate, claim/heartbeat/cancellation state, and
committed Artifact truth. The prior revisions and result mirrors are removed.
The cutover accepts only an empty target database.

`openapi.yaml` and the pinned generated TypeScript client are regenerated and
checked together. The maintained TUI uses generated HTTP types, strict SSE
state, same-key retry only before a response, durable cancellation, cursor
reload, visible display-only Fleet detail panels, and verified atomic Artifact
download. The bearer-auth clause is superseded by deterministic local scope.

## Consequences

- The HTTP, database, generated-client, and TUI changes must land together.
- Existing clients and databases are deliberately incompatible.
- Preparation failures are honest HTTP outcomes; post-header failures are one
  closed stream terminal.
- All durable/live/replay/reload semantic parity is centrally testable.
- Multi-worker correctness relies on transactional state, not process locks.
- Provider resources have one explicit owner and cleanup order.
- New detail kinds require an intentional schema-version change and exhaustive
  updates, not runtime registration.
- Skill catalog deepening, Memory, Child Sessions, Execution Modes, warm
  cross-Run interpreters, and Attachment download remain outside this decision.

## Migration and recovery

The cutover deletes the two existing revisions and creates revision
`019f5b3c96bd` as the sole baseline. It must pass empty PostgreSQL
upgrade/check/downgrade/re-upgrade evidence. There is no historical row
translation because the final input fingerprint cannot be reconstructed from
the old schema.

External rollback stops writers, removes the new application from traffic,
restores the pre-cutover snapshot to a new database or branch, and deploys the
exact previous application artifact. A database that accepted new-contract
writes is never downgraded in place, and new-contract Turns are not translated
backward.

## Validation

Implementation completion requires hermetic format, Ruff, `ty`, unit,
contract, E2E, generated OpenAPI/client, TUI, code-tree, docs, security,
dependency, release, wheel, and diff checks. Promotion additionally requires a
marked disposable PostgreSQL migration cycle and one non-skipping live
Daytona/DSPy lane tied to the exact candidate fingerprint.

## Rejected alternatives

- Retain `/api/chat` and optional Sessions: hides a stateful Turn behind a
  stateless action and keeps ambiguous identity.
- Loose event/detail/UI dictionaries: makes drift runtime-only and permits
  silent data loss.
- Separate lifecycle calls exposed to routes: lets callers violate atomic
  ordering and cleanup.
- A context-manager preparation API: hides the explicit resource handle needed
  across the streaming boundary.
- A third migration or dual codec: contradicts the empty single-baseline
  contract and cannot reconstruct ordered historical Attachments.
- Metadata-only Artifacts: leaves committed outputs uninspectable.
- A custom TUI fork: creates a UI subsystem when stable display-only detail
  panels preserve semantics through the maintained renderer.
  **Superseded for renderer choice first by ADR 0002 and then by ADR 0003:**
  pi-tui with static native scrollback is now the sole maintained terminal;
  coordinated Turn and shared projection semantics remain in force.
