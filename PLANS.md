# Fleet RLM MVP Phase Record

> Local, untracked verification record for `dev-0.7`.
> Refreshed against `ad0f12fd` on 2026-07-18.

## Purpose

This file records the feature contract delivered by each MVP phase and the
evidence that supports its status. It is not an implementation queue and does
not override the current source tree, tests, generated contracts, or tracked
documentation.

Evidence precedence is:

1. current implementation and generated contracts;
2. automated tests and validation commands at the reviewed git tip;
3. tracked architecture, reference, and decision documentation;
4. this local phase record;
5. archived `.scratch/` plans and historical receipts.

A phase is **implemented** when its feature contract and non-live exit gates are
present at the reviewed tip. A release candidate is **promoted** only when its
required live, CI, local, and human evidence all name the same exact git tip.

## Verified Status

| Phase | Capability | Status | Primary implementation evidence |
| --- | --- | --- | --- |
| 0 | Freeze the working MVP contract | Implemented and verified | `9e3c31c3` |
| 1 | Local BYOK scope and Deno/Daytona composition | Implemented and verified | `67ba3286`, `21b62c75` |
| 2 | Plain native `dspy.RLM` execution and observation | Implemented and verified | `0727477d`, `169aedae`, `ad0f12fd` |
| 3 | Native limits, usage, and Daytona admission | Implemented and verified | `5e4e64b2` |
| 4 | Typed Prediction result and private snapshot | Implemented and verified | `5e4e64b2`, `c7b76495` |
| 5 | Bounded Session context and on-demand history | Implemented and verified | `b64b8c56`, `3eee9e3d`, `39e7115c` |
| 6 | Explicit `dspy.Tool` capabilities and event views | Implemented and verified | `2ea187c5` |
| 7 | Durable private Session Workspace | Implemented and verified | `4e062821`, `cb1ff553` |
| 8 | Native SSE and maintained terminal workflow | Implemented and verified | `2b0e9b50`, `b482ab46` |
| 9 | Complete Daytona proof and release gate | Implementation complete; current-candidate promotion evidence pending | `a28faf0b`, `5e37b3d0`, `4cb67cb4`, `82b4cd92`, `cca3e6ea` |

The current non-live repository gates pass at `ad0f12fd`: `make check`,
`make test-deno`, `make check-security`, `make build-release`,
`make check-release`, and `git diff --check`.

## Current Product Contract

### Public surface

- One FastAPI backend under `src/fleet_rlm/`.
- One Session-first Turn endpoint:
  `POST /api/sessions/{session_id}/turns` with `Idempotency-Key`.
- Session, Attachment, committed Artifact, Skill discovery, and durable Run
  cancellation resources.
- AI SDK UI v1 chunks projected over SSE from transport-neutral Runtime Events.
- One maintained development client under `tools/fleet-tui/`.
- No `/api/v1`, top-level chat action, WebSocket execution, public Artifact
  creation, runtime-admin API, or compatibility backend.

### Runtime profiles

- `deno` is the canonical reduced local runtime: a real LM, a fresh native
  `dspy.RLM`, DSPy's default Deno/Pyodide interpreter, Attachment reads, and
  instruction Skills. It does not promise Daytona durability or Artifact
  promotion.
- `daytona` is the full Fleet runtime: Daytona Sandbox execution, Workspace
  Volume Scope, Attachment staging, private Session Workspace files, Artifact
  Candidate promotion, and commit-gated publication.
- Deterministic in-process composition is private test infrastructure, not a
  public Run Environment.

### Turn flow and ownership

```text
HTTP route
  -> TurnCoordinator.open()
  -> TurnLifecycle.begin()
  -> TurnPreparationModule.prepare()
  -> RLMRunner with one fresh native dspy.RLM
  -> TurnLifecycle.finish()
     -> result snapshot and Artifact byte promotion
     -> atomic Turn/Run/Checkpoint/Artifact commit or failure settlement
  -> TurnCoordinator terminal projection and cleanup
```

- `create_app()` installs routers, error/OpenAPI handlers, and the static
  in-memory bundled Skill catalog. Lifespan composition installs and disposes
  exactly one runtime inventory.
- `TurnLifecycle.finish()` owns successful publication and atomic Turn Commit.
  `TurnCoordinator` owns orchestration, terminal ordering, stream settlement,
  heartbeat coordination, and resource cleanup.
- The default Signature receives the request, bounded `session_context`,
  authorized `skill_cards`, and bounded Attachment metadata. Full Session
  history is excluded and available through `read_session_history`.
- Every Run gets a fresh RLM and interpreter context. Interpreter state may
  persist across iterations of that Run but never across Runs.

### Skills and host capabilities

- The bundled catalog currently contains `long-context` and
  `workspace-files`.
- A Turn sees bounded Skill Card metadata. Full instructions and declared
  resources load progressively, or by an exact version-pinned selection.
- The production `CapabilityRegistry` is an extension seam and is empty by
  default. Bundled Skills do not implicitly register executable host tools.
- HTTP requests select Skill identities and versions; they never provide
  executable Python or serialized tool objects.
- The retained SQL `SkillRow` is not the active catalog implementation.

### Persistence and files

- Alembic owns the live schema through one canonical baseline.
- `create_tables` is restricted to explicit SQLite test/local helpers.
- Committed Turn aggregates are the replay source.
- Attachment bytes and Daytona Artifact bytes use Workspace Volume Scope.
- Session Workspace files are immediate private state under the Session path;
  they survive failed Runs and Sandbox replacement independently of Turn
  Commit.
- Artifact Candidates remain private until verified byte promotion and atomic
  metadata commit both succeed.

### Terminal client

- `@earendil-works/pi-tui@0.80.10` is the only renderer; Node 22.19+ is
  required.
- The client consumes the backend HTTP/SSE contract and owns no model,
  provider credentials, Sandbox, or execution runtime.
- Live and durable Turn data share one projection/store path.
- Evidence is static, complete, and expanded in native terminal scrollback.
  Fleet does not capture the mouse or maintain transcript viewport state.
- The activity rail shows current phase/detail, elapsed time, steps, tools, and
  cancellation. The footer shows model, token usage, steps, and tools.
- Artifact download verifies content length and SHA-256 before an atomic rename.

## Phase 0 — Freeze the Working MVP Contract

### Delivered specification

- Characterization contracts lock the Session-first Turn and AI SDK UI stream
  behavior before the simplifying phases.
- Tests capture successful, failed, cancelled, replayed, Attachment, Artifact,
  and terminal ordering behavior without changing production code.

- Tests: `tests/contracts/backend/test_mvp_characterization.py` and
  `tests/contracts/backend/test_ai_sdk_ui_turn_contract.py`.
- Commit: `9e3c31c3`.
- Acceptance: focused characterization, contract, Deno, format, Ruff, and diff
  checks passed.

## Phase 1 — Local BYOK and Two Runtime Profiles

### Delivered specification

- Replaces caller-supplied auth/tenant identity with deterministic process-local
  User and Workspace scope for the local BYOK API.
- Establishes `deno` and `daytona` as the only public Run Environments.
- Splits common, Deno, Daytona, and private testing composition without runtime
  fallback or provider-credential exposure.

- Owners: `src/fleet_rlm/app.py`, `src/fleet_rlm/composition/`, API scope dependencies, and configuration.
- Commits: `67ba3286`, `21b62c75`.
- Acceptance: local-scope authorization, profile requirements, composition
  rollback, documentation, repository, and Deno gates passed.

## Phase 2 — Native `dspy.RLM`

### Delivered specification

- Constructs stock `dspy.RLM` through the pinned local contract seam rather
  than maintaining an Observable RLM subclass.
- Observes generated code and output at the interpreter boundary and host-tool
  activity through fresh wrapped `dspy.Tool` objects.
- Reconciles native DSPy trajectory details into the bounded Fleet Runtime Event
  stream without rewriting successful semantic output.

- Owners: `src/fleet_rlm/rlm/dspy_contract.py`, `src/fleet_rlm/rlm/runner.py`, and interpreter/tool observers.
- Commits: `0727477d`, `169aedae`, hardened by `ad0f12fd`.
- Acceptance: pinned-seam, native trajectory, interpreter observation, tool
  observation, runner, and Deno contracts passed.

## Phase 3 — Native Limits and Daytona Admission

### Delivered specification

- Uses native RLM iteration/call/output options and observed usage instead of a
  parallel mutable budget ledger.
- Bounds the process to at most eight acquiring or active Daytona Interpreter
  Leases and releases admission on all terminal paths.

- Owners: RLM options/usage contracts and Daytona admission/lease management.
- Commit: `5e4e64b2`.
- Acceptance: limit, usage, concurrency, cancellation, timeout, and full
  repository gates passed.

## Phase 4 — Typed Prediction Result

### Delivered specification

- Uses native typed DSPy Prediction output as the successful result contract.
- Validates declared structured output without rewriting accepted answer text.
- Daytona may store one private, commit-gated `result.json` derivative under the
  unique Run path. It is not a public Artifact or replay source.

- Owners: Task Contract projection, result policy, result snapshot sink.
- Commits: `5e4e64b2`, `c7b76495`.
- Acceptance: typed acceptance/rejection, snapshot commit/rollback, replay, SSE,
  and full repository gates passed.

## Phase 5 — Bounded Session Context

### Delivered specification

- Passes only a bounded Session context manifest to the default Signature.
- Keeps older committed messages host-side and exposes bounded, Session-scoped
  retrieval through `read_session_history`.
- Prevents cross-Session reads and excessive history payloads.

- Owners: context construction, Session history reader/tool, preparation.
- Commits: `b64b8c56`, `3eee9e3d`, `39e7115c`.
- Acceptance: context bounds, pagination, authorization, tool events, failure,
  cancellation, and replay contracts passed.

## Phase 6 — Explicit Tools and Event Views

### Delivered specification

- Standardizes Attachment, Artifact, Skill, history, and runtime capabilities as
  explicit `dspy.Tool` objects before RLM construction.
- Replaces heuristic argument/result redaction with host-owned, bounded,
  allowlisted event views.
- Tools without a declared event view expose identity, status, and safe failure
  metadata only.

- Owners: capability registry, preparation, tool observer, event view policy.
- Commit: `2ea187c5`.
- Acceptance: registration normalization, safe event projection, error closure,
  SSE, and repository gates passed.

## Phase 7 — Durable Session Workspace

### Delivered specification

- Adds path-safe private text files under
  `sessions/{session_id}/workspace/` in Daytona Workspace Volume Scope.
- Exposes list, read, write, and delete operations as bounded host tools.
- Provisions the canonical Volume directory skeleton during acquisition and
  fails closed on mount or provider conflicts.
- Preserves workspace state across failed Runs and Sandbox replacement; Deno
  advertises the capability as unavailable.

- Owners: workspace path policy/filesystem port, Daytona Volume adapter,
  acquisition layout, workspace tools.
- Commits: `4e062821`, hardened by `cb1ff553`.
- Acceptance: path traversal/symlink safety, replacement durability, event
  bounds, acquisition cleanup, and live workspace coverage are present.

## Phase 8 — Native SSE and pi-tui

### Delivered specification

- Uses native FastAPI SSE responses with one strict AI SDK UI v1 lifecycle.
- Keeps transport parsing, live/durable projection, atomic hydration, and screen
  rendering in separate TUI owners.
- Replaces the earlier Ink implementation with pi-tui, static expanded evidence,
  and native terminal scrollback.
- Adds explicit Skill selection/loading visibility, commands, autocomplete,
  cancellation, activity, usage, and verified Artifact download.

- Backend: `src/fleet_rlm/api/sse.py` and the Turn route.
- Client: `tools/fleet-tui/src/fleet-turn-stream.ts`, `sse.ts`, `tui/runner.ts`,
  `projection.ts`, `store.ts`, `application.ts`, `screen.ts`, and presenters.
- Commits: native SSE `2b0e9b50`; earlier Ink work `97a1e649` and `dd34deeb`;
  current pi-tui contract `b482ab46`.
- Acceptance: held-open streaming, strict chunks, retry/cancellation, parity,
  hydration, 10,000-row native scrollback, commands, cleanup, and TUI gates pass.

## Phase 9 — Daytona Proof and Promotion

### Delivered specification

- Provides `scripts/live_daytona_verify.py` and live backend scenarios that
  exercise the real FastAPI, DSPy, Daytona, Volume, Attachment, Skill, workspace,
  Artifact, typed result, reload, cleanup, and secret-isolation boundaries.
- Supports explicit approved Root/Sub Model overrides and bounded cleanup retry.
- Keeps live credentials in the host environment and writes only bounded local
  evidence.

### Verified implementation evidence

- Commits: `a28faf0b`, `5e37b3d0`, `4cb67cb4`, `82b4cd92`, `cca3e6ea`.
- A historical credentialed scenario passed the required semantic calls,
  workspace replacement, history reload, Artifact verification, cleanup, and
  secret-isolation checks.
- The current tree also passes the complete non-live validation matrix.

### Current promotion frontier

Phase 9 implementation is complete, but `ad0f12fd` is not yet a promoted
release candidate. The retained receipt for the earlier candidate records a
failed `first_turn` attempt and is historical evidence only. It is neither a
current blocker diagnosis nor a passing exact-tip receipt.

Promotion requires, in order:

1. run the verifier against the exact candidate SHA and retain a passing receipt:

   ```bash
   FLEET_LIVE=1 uv run python scripts/live_daytona_verify.py \
     --output .scratch/release-ready-mvp/assets/daytona-mvp-proof.json \
     --root-model <approved-root-model> \
     --sub-model <approved-sub-model>
   ```

2. record the exact SHA, model identifiers, provider cleanup, and passing
   invariants without secrets;
3. obtain required CI, local release, and human attestations for that same SHA;
4. promote only if none of those steps changes the candidate tree.

No current quota blocker is asserted. A future live attempt must report its own
observed failure category if it does not pass.

## Cross-Phase Invariants

- Provider credentials never enter API requests, Runtime Events, committed
  Turns, Sandbox-visible inputs, logs, or evidence receipts.
- Preparation validates ownership and selections before provider acquisition.
- Replay performs no provider preparation or execution.
- Exactly one terminal event is emitted after streaming starts.
- Failed, cancelled, or timed-out Runs do not advance committed Session history
  or publish Artifact identity.
- Interpreter Leases and newly created provider resources are released on every
  path; reused resources are not deleted by acquisition failure cleanup.
- Runtime Events remain transport-neutral; `api/sse.py` exclusively owns public
  SSE projection.
- Live and durable terminal projection use the same semantics.
- Generated contracts are `openapi.yaml` and
  `tools/fleet-tui/src/generated/openapi.ts`; `make api-sync` owns both.

## Deferred Beyond the MVP

- External multi-user authentication and tenant membership.
- Graphical or Web clients.
- Root/Child Session orchestration and delegated Session trees.
- Durable learned Memory.
- Warm interpreter state across Runs.
- Public runtime administration, optimization/evaluation APIs, or BYOK profile
  management.
- Bundled production host capability packages beyond the current instruction
  Skills.
- Deno durable Artifact promotion or Daytona feature parity.
- Windows terminal support.

These items require separate product decisions and are not implied by a completed
MVP phase.
