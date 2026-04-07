# Architecture

How the DSPy runtime, Daytona provider path, and web product surface fit together for this mission.

**What belongs here:** high-level ownership boundaries, runtime flows, integration seams, and invariants.
**What does NOT belong here:** per-feature TODOs or temporary debugging notes.

---

## Product Shape

`fleet-rlm` is a Daytona-backed DSPy application exposed through one product contract:
- FastAPI serves health/readiness endpoints, runtime/optimization APIs, websocket execution, and the packaged web UI.
- The workspace frontend consumes the shared `/api/v1/ws/execution` stream and renders live trace/reasoning state.
- Runtime settings exposes LM/Daytona diagnostics and local configuration write paths.
- Optimization exposes GEPA/MLflow-backed DSPy program optimization through the UI and API.

This mission refactors internals while preserving that full-stack contract.

## High-Level Layers

### Backend transport (`src/fleet_rlm/api`)
Owns the public HTTP/websocket contract, request validation, auth/identity normalization, runtime-settings routes, optimization routes, and websocket event emission.

### DSPy runtime (`src/fleet_rlm/runtime`)
Owns Signatures, `dspy.Module` composition, the ReAct chat agent, `dspy.RLM` runtime modules, streaming helpers, evaluation/optimization helpers, and tool orchestration.

### Daytona integration (`src/fleet_rlm/integrations/daytona`)
Owns sandbox/session/volume lifecycle, runtime preflight diagnostics, and provider-specific execution behavior beneath the shared runtime contract.

### Frontend (`src/frontend/src`)
Owns the workspace/settings/optimization surfaces, websocket event adaptation, runtime diagnostics presentation, and optimization form UX.

## Target Direction for This Mission

### 1. Slimmer DSPy Signatures
Signatures should describe semantic inputs/outputs and typed result shapes. They should not each carry large operational prompt blocks when those instructions belong in modules, tools, or shared runtime context.

### 2. Cleaner module composition
`dspy.Module` instances should compose other DSPy modules directly so evaluation, optimization, save/load, and test seams remain visible. Custom wrappers are acceptable only when they preserve or clarify this graph.

### 3. Explicit ReAct / RLM boundary
- `dspy.ReAct` remains the chat-time orchestration layer for tool selection.
- `dspy.RLM` remains the long-context/interpreter-backed execution layer.
- The boundary between them should be explicit, with less duplicated orchestration state living outside DSPy-native abstractions.

### 4. Full-stack contract preservation
Refactors must preserve:
- `/api/v1/ws/execution` as the canonical workspace stream
- Daytona-only runtime labeling and request controls
- frontend trace rendering from live `trajectory_step` / `reasoning_step` events
- runtime settings and optimization route shapes consumed by the frontend

## Critical Flows

### Workspace execution flow
1. Frontend submits a message over `/api/v1/ws/execution`.
2. Backend prepares the shared chat runtime and attaches Daytona execution hints.
3. `RLMReActChatAgent` uses `dspy.ReAct` plus explicit tools/runtime modules.
4. Long-context or delegated work uses interpreter-backed `dspy.RLM` modules.
5. Streamed events are emitted to the websocket and adapted by the frontend into transcript/trace UI.

### Runtime settings flow
1. Frontend loads runtime status/settings.
2. Backend returns current diagnostic/config snapshots.
3. User can patch local settings and trigger LM/Daytona smoke tests.
4. Resulting status/guidance must match what the workspace warning state communicates.

### Optimization flow
1. Frontend loads optimization availability status.
2. User submits dataset path + DSPy program spec (`module:attr`).
3. Backend resolves/instantiates the program and runs GEPA/MLflow-backed optimization.
4. Structured result metadata returns to the UI.

## Invariants

- The public runtime contract remains Daytona-only.
- Frontend consumers must not need to understand backend refactor details.
- `dspy.Module` graphs stay instantiable from quality/optimization tooling.
- Runtime settings and optimization responses remain stable enough for generated/frontend clients.
- The live Daytona path must be end-to-end testable by mission completion.
