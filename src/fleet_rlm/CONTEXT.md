# Backend runtime

The backend runtime context names the execution roles and migration boundaries of Fleet RLM.

## Language

**RLM-native Backend**:
A backend whose primary engine for open-ended agentic work is `dspy.RLM`, while bounded DSPy modules and deterministic Python remain responsible for narrow prediction and policy work.
_Avoid_: RLM-only backend, legacy runtime with an RLM feature

**Execution Backend**:
The server-selected runtime implementation that executes a turn. It is distinct from client-visible turn controls and behavior modes.
_Avoid_: execution mode, client-selected backend

**Compatibility Runtime**:
The legacy agent runtime retained temporarily for migration and rollback while the RLM-native path earns and sustains promotion evidence.
_Avoid_: primary runtime, permanent duplicate implementation

**Runtime Event**:
The backend-neutral record of turn progress and completion consumed by observability and transport projection.
_Avoid_: SSE part, WebSocket frame

**Managed Target**:
A server-registered DSPy module or catalog-resolved Skill with a versioned Metric Profile, proposer policy, concurrency cap, artifact codec, and promotion gates.
_Avoid_: arbitrary import, filesystem target

**Selection Set**:
The explicit dataset partition passed to GEPA as `valset` and used during Pareto search. It is optimizer-visible and is not a holdout.
_Avoid_: validation holdout, promotion set

**Promotion Test**:
The sealed dataset partition hidden from GEPA and evaluated once on the baseline and selected winner after compilation.
_Avoid_: GEPA valset, selection set

**Approved Artifact**:
An immutable state-only JSON module artifact or Skill Markdown artifact that passed sealed-test and round-trip gates and received human approval. Approval does not activate it.
_Avoid_: checkpoint, GEPA winner, active prompt

**Activation Pointer**:
The tenant- and workspace-scoped reference selecting an approved artifact for one Managed Target, with one retained previous version for rollback.
_Avoid_: global default, promotion draft
