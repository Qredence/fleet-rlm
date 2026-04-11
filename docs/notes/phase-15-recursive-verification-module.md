# Phase 15 recursive verification module note

Preserve the fleet-rlm worker boundary, Agent Framework hosted orchestration, Daytona Sandbox/Volume behavior, and the current websocket/frontend contract. Phase 15 adds one more DSPy-native recursive worker module for semantic verification of decomposition-driven results, without moving execution ownership out of the Daytona-backed worker.

## What moved into DSPy

Phase 15 promotes one more worker-side recursive sub-decision into typed DSPy code:

- `VerifyRecursiveAggregation` in `src/fleet_rlm/runtime/agent/signatures.py`
- `VerifyRecursiveAggregationModule` in `src/fleet_rlm/runtime/agent/recursive_verification.py`

This module decides:

- whether the bounded aggregate of subquery results is semantically sufficient
- which important evidence is still missing
- which contradictions remain unresolved
- which concise verified summary should be handed to recursive reflection next

The live adapter stays inside the worker/runtime layer at the recursive delegate seam in `src/fleet_rlm/runtime/agent/recursive_runtime.py`.

## How this complements context assembly, decomposition, and reflection

The earlier worker-native recursive modules keep their roles:

- `AssembleRecursiveWorkspaceContextModule` still selects bounded Daytona-backed handles and evidence
- `PlanRecursiveSubqueriesModule` still decides whether and how to fan out recursive subqueries
- `ReflectAndReviseWorkspaceStepModule` still decides whether the worker should recurse, repair, finalize, or request human review

The new verification module runs after Python/runtime has already executed the bounded child subqueries and aggregated their results. It does not replace reflection; it improves the inputs reflection sees by adding a verified summary plus explicit missing-evidence and contradiction signals.

## How Daytona-backed ownership was preserved

The verification module does not execute code, launch child runs, manage batching, or move durable state into orchestration metadata.

It only consumes bounded worker-prepared inputs such as:

- the current delegated request
- the already assembled recursive context
- a compact decomposition-plan summary
- bounded subquery output summaries
- compact Daytona handle and sandbox evidence summaries

Python/runtime still owns child execution, batching, result collection, and operational aggregation. Daytona still owns sandbox lifecycle, interpreter state, and durable memory/volume storage.

## How GEPA is used offline

Offline optimization lives in `src/fleet_rlm/runtime/quality/optimize_recursive_verification.py`.

That entrypoint:

- loads representative JSON or JSONL recursive verification traces
- converts them into typed DSPy examples for the verification signature
- applies an explicit GEPA feedback metric centered on verification quality, boundedness, and usefulness for the next recursive decision
- saves optimized DSPy artifacts plus a manifest

Artifact output defaults to Daytona-backed quality storage under `/home/daytona/memory/artifacts/quality/recursive-verification/` when available, and otherwise falls back to local `.data/quality-artifacts/recursive-verification/` storage for offline development.

## What stays outside DSPy on purpose

These boundaries remain unchanged:

- Agent Framework still owns hosted orchestration, resumability, checkpoints, and HITL
- FastAPI and websocket layers still own transport, auth, and envelope serialization
- Daytona still owns sandbox creation, process execution, interpreter state, and durable memory
- GEPA still stays out of the live websocket request path

## Next phase

The next phase should keep the same worker/orchestration/transport boundaries and improve the recursive worker stack with richer structured evidence contracts or a narrow recursive repair-execution seam, rather than widening orchestration or websocket ownership.
