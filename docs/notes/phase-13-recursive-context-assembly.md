# Phase 13 recursive context assembly note

Preserve the fleet-rlm worker boundary, Agent Framework hosted orchestration, Daytona Sandbox/Volume behavior, and the current websocket/frontend contract. Phase 13 adds one more DSPy-native recursive worker module for assembling the next recursive workspace context from Daytona-backed memory/evidence, without changing orchestration ownership.

## What moved into DSPy

Phase 13 promotes one more worker-side recursive sub-decision into typed DSPy code:

- `AssembleRecursiveWorkspaceContext` in `src/fleet_rlm/runtime/agent/signatures.py`
- `AssembleRecursiveWorkspaceContextModule` in `src/fleet_rlm/runtime/agent/recursive_context_selection.py`

The module decides which bounded Daytona-backed handles and recent evidence should be carried into the next recursive pass, and produces a compact assembly summary for that pass.

The live adapter stays inside the worker/runtime layer at the recursive delegate seam in `src/fleet_rlm/runtime/agent/recursive_runtime.py`.

## How long memory and evidence stay Daytona-backed

The new DSPy module does not copy full durable memory, raw interpreter state, or Agent Framework workflow state into orchestration state.

It only consumes compact worker-resolved catalogs such as:

- Daytona volume, workspace, sandbox, and optional memory handles
- staged `context_path` handles
- bounded summaries of recent sandbox/tool/code evidence
- latest-result summaries and recursion loop metadata

Durable memory still lives in Daytona volumes, interpreter/session state still belongs to Daytona, and the worker only forwards selected handles plus bounded summaries into the next recursive pass.

## How this complements the reflection module

The existing `ReflectAndReviseWorkspaceStepModule` keeps its role: deciding whether the worker should recurse, repair, finalize, or request human review.

The new context-assembly module runs before that reflection step when enabled and improves the inputs that reflection sees by:

- selecting the most relevant Daytona-backed memory handles
- selecting the most relevant recent evidence ids
- assembling a bounded context summary for the next pass
- explicitly capturing why other context was omitted

This keeps recursive context selection and recursive branching as separate worker-native DSPy responsibilities.

## How GEPA is used offline

Offline optimization lives in `src/fleet_rlm/runtime/quality/optimize_recursive_context_selection.py`.

That entrypoint:

- loads representative JSON or JSONL recursive context-selection traces
- converts them into typed DSPy examples for the selection signature
- applies an explicit GEPA feedback metric centered on relevance and boundedness
- saves optimized DSPy artifacts plus a manifest

Artifact output defaults to Daytona-backed quality storage under `/home/daytona/memory/artifacts/quality/recursive-context-selection/` when available, and otherwise falls back to local `.data/quality-artifacts/recursive-context-selection/` storage for offline development.

## What stays outside DSPy on purpose

These boundaries remain unchanged:

- Agent Framework still owns hosted orchestration, resumability, checkpoints, and HITL
- FastAPI and websocket layers still own transport, auth, and envelope serialization
- Daytona still owns sandbox creation, process execution, interpreter state, and durable memory
- GEPA still stays out of the live websocket request path

## Next phase

The next phase should keep the same worker/orchestration/transport boundaries and add only one more recursive worker-native DSPy policy at a real seam, likely by improving recursive repair execution or recursive evidence validation with stronger offline datasets and metrics.
