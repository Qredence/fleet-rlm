# Phase 14 recursive decomposition module note

Preserve the fleet-rlm worker boundary, Agent Framework hosted orchestration, Daytona Sandbox/Volume behavior, and the current websocket/frontend contract. Phase 14 adds one more DSPy-native recursive worker module for bounded recursive decomposition, without moving execution ownership out of the Daytona-backed worker.

## What moved into DSPy

Phase 14 promotes one more worker-side recursive sub-decision into typed DSPy code:

- `PlanRecursiveSubqueries` in `src/fleet_rlm/runtime/agent/signatures.py`
- `PlanRecursiveSubqueriesModule` in `src/fleet_rlm/runtime/agent/recursive_decomposition.py`

This module decides:

- whether the next recursive pass should stay single-pass or fan out
- which bounded semantic subqueries to issue next
- which batching shape Python/runtime should use
- how the resulting subquery outputs should be aggregated

The live adapter stays inside the worker/runtime layer at the recursive delegate seam in `src/fleet_rlm/runtime/agent/recursive_runtime.py`.

## How this aligns with the Daytona RLM pattern

The split stays consistent with the Daytona RLM model:

- DSPy handles the semantic decomposition decision
- Python/runtime code handles looping, batching, child execution, parsing, and aggregation
- Daytona sandboxes, volumes, interpreter state, and durable memory remain the execution substrate

In the live path, the worker now asks DSPy how to decompose the next recursive pass when `recursive_decomposition_enabled` is on, then executes the proposed bounded subqueries inside the existing Daytona-backed child interpreter flow.

## How Daytona-backed ownership was preserved

The decomposition module does not execute code, create sandboxes, or copy durable memory into orchestration state.

It only consumes bounded worker-prepared inputs such as:

- the current delegated request
- the already assembled recursive context
- compact Daytona handle summaries
- bounded loop-state metadata
- a bounded subquery budget

Python/runtime still owns the actual child runs and aggregation, and durable memory stays in Daytona volume storage rather than Agent Framework checkpoints or websocket transport state.

## How this complements the earlier recursive modules

The existing worker-native recursive modules keep their roles:

- `AssembleRecursiveWorkspaceContextModule` still selects bounded Daytona-backed handles and recent evidence
- `ReflectAndReviseWorkspaceStepModule` still decides whether the worker should recurse, repair, finalize, or request human review

The new decomposition module sits earlier at the delegate seam and complements them by deciding how the next recursive pass should be split into bounded semantic sub-work before execution starts.

## How GEPA is used offline

Offline optimization lives in `src/fleet_rlm/runtime/quality/optimize_recursive_decomposition.py`.

That entrypoint:

- loads representative JSON or JSONL recursive decomposition traces
- converts them into typed DSPy examples
- applies an explicit GEPA feedback metric centered on decomposition quality, boundedness, and aggregation usefulness
- saves optimized DSPy artifacts plus a manifest

Artifact output defaults to Daytona-backed quality storage under `/home/daytona/memory/artifacts/quality/recursive-decomposition/` when available, and otherwise falls back to local `.data/quality-artifacts/recursive-decomposition/` storage for offline development.

## What stays outside DSPy on purpose

These boundaries remain unchanged:

- Agent Framework still owns hosted orchestration, resumability, checkpoints, and HITL
- FastAPI and websocket layers still own transport, auth, and envelope serialization
- Daytona still owns sandbox creation, process execution, interpreter state, and durable memory
- GEPA still stays out of the live websocket request path

## Next phase

The next phase should keep the same worker/orchestration/transport boundaries and improve how decomposition-driven subquery results are synthesized or validated, ideally by tightening aggregation quality and adding richer representative offline datasets without widening orchestration or transport ownership.
