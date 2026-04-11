# Phase 12 DSPy recursive module + GEPA note

Preserve the fleet-rlm worker boundary, Agent Framework hosted orchestration, Daytona Sandbox/Volume behavior, and the current websocket/frontend contract. Phase 12 adds one DSPy-native recursive reasoning module inside the worker and an offline GEPA optimization path for it, without changing live orchestration or execution ownership.

## What moved into DSPy

Phase 12 makes one narrow recursive worker behavior explicit as DSPy code:

- `ReflectAndReviseWorkspaceStep` in `src/fleet_rlm/runtime/agent/signatures.py`
- `ReflectAndReviseWorkspaceStepModule` in `src/fleet_rlm/runtime/agent/recursive_reflection.py`

The module decides whether the current recursive workspace step should:

- recurse
- finalize
- request human review
- repair and retry

The live adapter stays inside the worker/runtime layer at the recursive delegate seam in `src/fleet_rlm/runtime/agent/recursive_runtime.py`.

## How long memory and evidence stay Daytona-backed

The new DSPy module does not ingest full durable memory or move execution state into Agent Framework workflow state.

It only consumes summarized worker-resolved inputs such as:

- Daytona volume and workspace handles
- selected runtime metadata like `volume_name`, `workspace_path`, and `sandbox_id`
- compacted sandbox evidence and recent tool/code results
- current recursion depth and retry state

Durable memory still lives in Daytona volumes, execution still happens in Daytona sandboxes, and stateful interpreter context remains owned by Daytona.

## How GEPA is used offline

Offline optimization lives in `src/fleet_rlm/runtime/quality/optimize_reflect_and_revise.py`.

That entrypoint:

- loads representative JSON or JSONL recursive traces
- converts them into typed DSPy examples for the reflection signature
- applies an explicit GEPA feedback metric centered on `next_action`, `revised_plan`, and rationale quality
- saves optimized DSPy artifacts plus a manifest

Artifact output defaults to Daytona-backed quality storage under `/home/daytona/memory/artifacts/quality/reflect-and-revise/` when available, and otherwise falls back to local `.data/quality-artifacts/reflect-and-revise/` storage for offline development.

## What stays outside DSPy on purpose

These boundaries remain unchanged:

- Agent Framework still owns outer orchestration, resumability, checkpoints, and HITL
- FastAPI still owns auth, socket lifecycle, parsing, and envelope serialization
- Daytona still owns sandbox creation, process execution, interpreter state, and durable memory
- GEPA still stays out of the live websocket request path

## Next phase

The next phase should keep the same boundary discipline and add only one more worker-native DSPy module at a time, ideally by promoting another already-real recursive policy decision into a typed signature plus offline optimization dataset.
