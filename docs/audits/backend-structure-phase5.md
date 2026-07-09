# Backend Structure After Phase 5

Audit date: 2026-07-09
Scope: Backend package structure checkpoint after Phase 5 and before Phase 6.
Status: Structure, ownership, and import-boundary audit only. Phase 6 is not started here.

## Current Tree Source

Generated from:

```zsh
git ls-files src/fleet_rlm | sort
```

Top-level tracked package counts at this checkpoint:

| Package | Files | Ownership |
|---|---:|---|
| `api/` | 112 | FastAPI app, routers, schemas, dependencies, auth, SSE/WS transport preparation, and runtime service seams |
| `daytona/` | 8 | Canonical Daytona facade package |
| `integrations/daytona/` | 17 | Legacy Daytona implementation and compatibility import surface |
| `tools/` | 9 | Canonical RLM-facing tool behavior, descriptors, path/session helpers |
| `runtime/tools/` | 20 | `@tool_fn` discovery stubs, runtime binding, legacy host/sandbox wrappers |
| `artifacts/` | 5 | Artifact schemas, approved roots, and Daytona-backed storage helpers |
| `files/` | 5 | Upload staging, `AttachmentRef`, `AttachedFiles`, and attachment resolution |
| `skills/` | 27 | Skill catalog, loader, public service serialization, writes, approval, audit, install/update lifecycle |
| `rlm/` | 6 | Opt-in `direct_rlm` backend implementation |
| `runtime/` | 67 | Legacy `AgentRuntime`, runtime events, schemas, modules, and compatibility execution path |
| `quality/` | 31 | Evaluation, optimization, GEPA, and trace-bundle utilities |
| `cli/` | 16 | CLI and terminal UI |
| `utils/` | 11 | Shared low-level utilities |
| `ui/` | 2 | Backend helpers for built UI/static serving |

## Canonical Packages

### `api/`

`api/` owns transport and HTTP concerns: app assembly, auth, routers, schemas,
SSE/WS projection, and transport-neutral runtime services. API code may import
service/facade layers, but service/facade layers should not import API routers or
HTTP exception mapping.

The tree already contains trace and observability-facing API surfaces such as
`api/routers/traces.py`, `api/runtime_services/trace_service.py`, and
`api/runtime_services/session_trace_debug.py`. These are existing surfaces to
respect during Phase 6, not new work from this checkpoint.

### `daytona/`

`daytona/` is the stable Daytona import surface. The package is intentionally
thin and delegates to `integrations.daytona` while the facade transition remains
active. Importing `fleet_rlm.daytona` must not require live Daytona credentials.

### `tools/`

`tools/` owns canonical RLM-facing tool implementations and policy metadata.
`tools/registry.py` owns `ToolDescriptor`, `ToolExposurePolicy`, and
`filter_tool_names()`. `runtime/tools/registry.py::discover_tools()` remains the
single policy filtering point, and `runtime/tools/binding.py::bind_runtime_tools()`
only binds runtime-backed implementations or removes interpreter-only tools when
no interpreter is available.

### `artifacts/`

`artifacts/` owns approved artifact roots, artifact schemas, safe references, and
Daytona-backed artifact I/O. `artifacts/storage.py` is intentionally a
backward-compatible facade over `paths.py` and `io.py`.

### `files/`

`files/` owns upload staging, `AttachmentRef`, `AttachedFiles`, and attachment
resolution. Attachment resolution remains metadata-only; attachment contents are
not read by default and are not injected into prompts.

### `skills/`

`skills/` owns catalog/repository/loader behavior, public-safe serialization in
`skills/service.py`, script execution, writes/approval/audit, and remote
install/provenance/update lifecycle. `skills/install_policy.py` now depends on a
local Protocol shape instead of importing `api.config.AppConfig`, keeping the
skills package independent from API types.

### `rlm/`

`rlm/` owns the opt-in `direct_rlm` backend. This checkpoint does not make
`direct_rlm` the default and does not add trace recorder, transcript mapping, or
MLflow wiring to the RLM path.

## Compatibility Packages

### `integrations/daytona/`

`integrations/daytona/` remains the legacy Daytona implementation substrate and
compatibility import surface. Do not remove or rename public compatibility
imports without explicit tests.

### `runtime/tools/`

`runtime/tools/` remains the discovery/binding/compatibility layer. Stubs marked
with `@tool_fn` preserve public tool names while delegating canonical behavior to
`tools/` where available. Host-only helpers remain in
`runtime/tools/host_filesystem.py`; Daytona-backed file behavior is canonical in
`tools/filesystem.py`.

### `runtime/`

`runtime/` remains the legacy `AgentRuntime` path and event/schema compatibility
layer. `RuntimeEvent` remains the internal event contract shared by SSE,
WebSocket, legacy runtime, and opt-in direct RLM.

## Intentionally Thin Facades And Stubs

- `daytona/*.py` facade modules delegate to `integrations.daytona.*`.
- `runtime/tools/filesystem.py` exposes discovery stubs and delegates to
  `tools/filesystem.py`.
- `runtime/tools/artifacts.py` exposes discovery stubs and delegates to
  `tools/artifacts.py` through runtime binding.
- `runtime/tools/skill_tools.py` preserves backward-compatible re-exports for
  skill tool implementations.
- `artifacts/storage.py` preserves a backward-compatible artifact storage import
  surface after the Phase 5 paths/I/O split.
- `skills/__init__.py` retains lazy/backward-compatible exports expected by
  runtime and quality callers.

## Boundary Checks

Targeted scans found no stale references claiming that attachments, attachment
refs, or artifact tools are unimplemented. `PLANS.md` already marks Phase 5
complete and Phase 6 next, so no roadmap status edit is required for this
checkpoint.

Layer boundary checks:

- `skills/` has no runtime import of `fleet_rlm.api.*`.
- `tools/`, `artifacts/`, `files/`, and `daytona/` do not import API routers.
- `bind_runtime_tools()` does not apply a second policy filter.
- Discovered runtime tools have descriptors.
- Default discovery hides sandbox-required tools when `sandbox_available=False`.

## Deferred Cleanup

- Keep `integrations/daytona/` as the implementation substrate until a later
  migration can move behavior behind the `daytona/` facade safely.
- Keep `runtime/tools/` compatibility stubs until public tool-name compatibility
  can be retired with explicit tests.
- Add an O(1) artifact-id index only if artifact lookup volume makes the current
  path scan too expensive.
- Keep existing trace/MLflow/observability surfaces in `api/`,
  `integrations/observability/`, and `quality/` unchanged until Phase 6.
- Do not move quality/GEPA modules into observability before the Phase 8 quality
  lane.

## Phase 6 Readiness

Phase 6 can start cleanly after this checkpoint if the targeted validation lane
passes. The implementer should treat existing trace and observability modules as
current surfaces to integrate with, but must not infer that Phase 6 already has a
new `src/fleet_rlm/observability/` or `src/fleet_rlm/traces/` package. Any new
recorder, transcript mapping, performance summary, or MLflow span ingestion work
belongs to Phase 6 proper.
