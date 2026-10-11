# Turn walkthrough and resource owners

The deterministic scenario is
`tests/sessions/test_turn_coordinator_execution.py::test_turn_walkthrough_preserves_child_isolation_memory_commit_and_replay`.
It uses the real Turn coordinator, DSPy RLM, isolated interpreters, settlement,
and canonical memory storage with deterministic model responses.

| Stage | Direct call chain | Resource owner |
| --- | --- | --- |
| Application | `create_app` → `ApplicationComposition` → `daytona_lifespan` → `build_daytona_composition` → `RuntimeInventory` | Application composition retains runtime, deferred disposal, workers, bridge, and database |
| Claim and prepare | `TurnRuntime.open` → lifecycle `begin` → `TurnPreparationPlan.prepare` → `prepare_turn` → `DaytonaRunPreparation.acquire_environment` | Turn owns the prepared environment; Daytona runtime owns retained remote resources |
| Root | `RLMRunner.stream` → configured interpreter factory → native DSPy `RLM.acall` | DSPy owns invocation interpreter shutdown, history, reasoning iterations, and semantic sub-model calls |
| Child | root `rlm_query` → `RecursiveRLMExecutor` → child factory → isolated child invocation → result harvest → confirmed cleanup | Fleet child executor owns depth-one investigation and shared admission; Daytona owns remote retirement |
| Memory | capability collector → outcome memory candidates → successful settlement → `OwnedPostCommitMemoryPromotion` → `WorkspaceMemory` | Workspace services own canonical memory and promotion recovery; Turn settlement authorizes promotion |
| Commit | `TurnRuntime._finish_with_trace` → `RunSettlementPlan.finish` → durable Run transition and committed parts | Turn lifecycle owns commitment; observations cannot authorize or prevent it |
| Replay | same `OpenTurnCommand` → committed receipt → replay projection | Durable Run state supplies replay; no interpreter or child is acquired |
| Cleanup | prepared Turn close → cleanup supervisor drain → runner close → preparation close → Daytona disposal → database close → bridge release | Application retains unresolved disposal and retries without transferring loop-bound clients |

Every root receives child tools and a child factory. Delegation is a reasoning
choice; recursion has no enable switch. Children retain the existing depth-one
boundary and cannot access root interpreter globals.

Invocation interpreter lifetime and retained Sandbox lifetime are separate.
Stopping a DSPy invocation does not independently authorize remote deletion.
A pending cleanup keeps its owner and admission permit until confirmation.
Route dependencies are published only after readiness; shutdown clears route
readiness while the application composition retains resource ownership.

Execution event values live in `rlm/event_types.py`; tool observations and DSPy
callbacks live in `rlm/events.py`. Committed-part conversion stays in
`sessions/committed_turn.py`, separate from the Turn aggregate and observation
services. Configuration editor policy and environment references share
`config/catalog.py`; loading, discovery, and generated documentation consume it.
