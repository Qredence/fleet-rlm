# Fleet RLM Refactor — What Has Been Done

## Scope

This document summarizes completed work on branch `chore/daytona-integration-hardening` through Phase 5 of the Fleet RLM backend refactor.

## Completed foundation

### Phase 1 — FastAPI SSE Transport Boundary

Fleet now has a transport-neutral chat path built around `ChatExecutionContext`, `stream_turn()`, and `RuntimeEvent` projection. `/api/chat` is the canonical AI SDK UIMessage v1 SSE endpoint, mounted at app root, while the existing WebSocket execution path remains supported.

Completed contracts:

- `POST /api/chat` exists as the AI SDK UIMessage v1 SSE endpoint.
- WebSocket execution still works.
- `ChatExecutionContext` exists.
- `stream_turn()` exists.
- `RuntimeEvent` remains the internal execution event contract.
- SSE projection is separate from runtime execution.

### Phase 2A — Execution Backend Seam

Fleet introduced an internal `ExecutionBackend` seam behind `stream_turn()`.

Completed contracts:

- `legacy_agent_runtime` remains the default backend.
- `direct_rlm` is opt-in only through internal/config controls.
- `ChatRequest` does not expose `execution_backend`.
- `stream_turn()` dispatches through `ExecutionBackend`.
- Default runtime behavior remains unchanged.

### Phase 2A.1 — Merge-Gate Hardening

The `/api/chat` SSE path now uses the same shared interpreter pool lifecycle as the WebSocket path. Prepare/startup failures are sanitized for clients and detailed server-side diagnostics are preserved in logs.

Completed contracts:

- SSE and WebSocket share `InterpreterPoolDeps`.
- Client-facing prepare/startup errors are sanitized.
- Detailed failures are logged server-side.
- Legacy runtime remains default.
- Chat request and OpenAPI contracts remain stable.

### Phase 2A.2 — Test and Contract Cleanup

Large runtime/API tests were split and common fakes/fixtures were extracted.

Completed outcomes:

- Focused `stream_turn` test modules.
- Shared API/runtime fakes and fixtures.
- Better test isolation.
- No production behavior change.

### Phase 2B — DirectRLMRunner Skeleton

A `src/fleet_rlm/rlm/` package was introduced and `ExecutionBackend.direct_rlm` dispatches to `DirectRLMRunner`.

Completed outcomes:

- `src/fleet_rlm/rlm/runner.py` exists.
- `src/fleet_rlm/rlm/errors.py` exists.
- Stubbed direct RLM path emits structured runtime events.
- Legacy backend remains default.

### Phase 2C — Direct RLM Golden Path

The opt-in direct RLM backend can run a simple RLM turn through a pooled Daytona interpreter.

Completed outcomes:

- `EXECUTION_BACKEND=direct_rlm` can run a basic RLM path.
- `DirectRLMRunner` reuses `agent_runtime.interpreter`.
- Blocking DSPy execution is offloaded with `asyncio.to_thread`.
- Direct RLM emits runtime-compatible status, trajectory, text, done, and error events.
- Live integration test remains skipped unless Daytona and LLM credentials are present.

### Phase 2D — RuntimeEvent Parity

Direct RLM and legacy runtime now share a stronger internal event vocabulary.

Completed outcomes:

- Direct RLM emits `TURN_INPUTS`.
- Direct RLM terminal `DONE` payload includes schema and history metadata.
- Trajectory replay maps sandbox execution into shared runtime events.
- SSE projection remains backend-agnostic.

## Completed Skills work

### Phase 3A–3C — Skills Package Foundation, Catalog, Loader, Selection

Skills were promoted into a first-class backend subsystem.

Completed outcomes:

- `src/fleet_rlm/skills/` package created.
- Skill schemas, catalog, repository, loader, selection, active skill serialization, permissions, validation, and sync primitives exist.
- Directory-style skills and legacy flat skills are supported.
- Selected skills are injected through `ActiveSkills`.
- Visibility gating prevents invisible skills from being selected or loaded.

### Phase 3D — FastAPI Skill APIs, Read-only

Read-only skill APIs were added under `/api/v1/skills`.

Completed outcomes:

- List, detail, select, load, validate, resource inventory, and resource read endpoints.
- Public-safe serialization through `skills/service.py`.
- Typed domain errors in `skills/errors.py`.

### Phase 3E — RLM Skill Tools, Read-only

Read-only skill tools were exposed to the RLM/tool layer.

Completed tools:

- `list_skills`
- `load_skill`
- `read_skill_resource`

### Phase 3F — Trusted Script Execution

Trusted selected-skill scripts can run inside Daytona only.

Completed outcomes:

- `run_skill_script` executes trusted scripts inside Daytona.
- Script execution resolves selected skills at call time.
- Large logs are stored in Daytona.
- Failed public payloads omit stdout/stderr.
- Timeout is capped.

Deferred:

- Scaffold script materialization only if product requires bundled scaffold scripts inside Daytona.

### Phase 3G — Skill Writes and Approval Workflow

Skill write workflows and staged approval were implemented.

Completed outcomes:

- `skills/writes.py`, `skills/approval.py`, and `skills/audit.py` exist.
- Server-derived actor model for HTTP writes.
- Staged changes can be approved or rejected.
- Built-in and higher-precedence skill shadowing protections.
- OpenAPI synced.

### Phase 3H — Remote Skill Installs, Provenance, Security, and Update Lifecycle

Remote skill install/update workflows were implemented.

Completed outcomes:

- Remote URL and bundle install policy seam.
- Provenance records with content hashes and trust overlay.
- Static security scan heuristics.
- Quarantine for blocked installs.
- HTTPS-only remote fetch with SSRF guards and allowlist.
- URL and bundle install APIs.
- Provenance and update APIs.

Deferred:

- RLM `install_skill` / `update_skill` tools.
- Admin quarantine listing API.
- Frontend skill UI.
- Bundle/manifest update apply path.

## Completed Daytona work

### Phase 4 — Daytona Facade Split

Fleet now has a canonical Daytona facade package.

Completed package:

```text
src/fleet_rlm/daytona/
  __init__.py
  interpreter.py
  sandbox.py
  volume.py
  files.py
  workspace.py
  session_state.py
  diagnostics.py
```

Completed outcomes:

- `fleet_rlm.daytona.*` is the stable import surface.
- `integrations.daytona` compatibility is preserved through re-exports/delegation.
- Low-risk runtime and direct RLM imports moved to the facade.
- Default tests do not require live Daytona.

## Completed Tools, Artifacts, and Attachments work

### Phase 5 — Tools, Artifacts, Attachments

Fleet now exposes controlled runtime capabilities through safe Daytona-backed tools and structured attachment/artifact systems.

Completed slices:

- 5A Tools foundation.
- 5B Structural cleanup.
- 5C Attachment upload and chat refs.
- 5D Controlled artifact tools.
- 5 closure: paths/I/O split, legacy `attached_files` wiring, and `read_file` spill.

Completed packages:

```text
src/fleet_rlm/tools/
src/fleet_rlm/artifacts/
src/fleet_rlm/files/
src/fleet_rlm/runtime/tools/artifacts.py
```

Completed capabilities:

- Policy-filtered tool discovery.
- Daytona-backed filesystem tools.
- Controlled write tools.
- Artifact creation/update/read/list behavior.
- Large output spill to artifacts.
- Upload endpoint: `POST /api/v1/files/upload`.
- Chat `attachment_refs`.
- `AttachedFiles` as `SandboxSerializable` RLM context.

## Current completed-state summary

Through Phase 5, Fleet has stabilized the transport/runtime seam, introduced opt-in direct RLM, made Skills/Daytona/Tools/Artifacts/Files first-class subsystems, and preserved legacy runtime compatibility.

The next named phase is Phase 6: Trace, Transcript, Performance, and MLflow.
