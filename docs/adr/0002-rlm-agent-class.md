# First-class RLMAgent for the rlm execution path

## Context

The `rlm` execution path had no first-class agent identity. It lived as an
~880-line `_run_rlm()` method (lines 1135-1655) inside `EscalatingFleetModule`,
which also owned the three RLM variants (`_rlm`, `_workspace_rlm`,
`_url_document_rlm`) and all retry/parse-recovery/timeout/fallback resilience.
The recursion primitive was scattered across `rlm_query`/`rlm_query_batched`
(agent-level, rejected inside the sandbox bridge to prevent infinite
recursion), `sub_rlm`/`sub_rlm_batched` (interpreter child delegation),
`delegate_to_rlm`/`delegate_to_rlm_batched` (in-process ReAct tools), and
`llm_query` (lightweight semantic sub-LM calls). Once ADR-0001 removed
"escalation", the `EscalatingFleetModule` name no longer described its job.

## Decision

Introduce a first-class `RLMAgent` class as the owner of the `rlm` execution
path. `RLMAgent` owns: the Daytona interpreter binding, the three RLM variants
(standard / workspace / url-document), the retry/fallback/resilience logic
formerly inlined in `_run_rlm()`, and exposes `rlm_query()` as the canonical
sub-agent spawning primitive (consolidating `sub_rlm` / `delegate_to_rlm` as
part of Phase 2.3 delegation unification).

`EscalatingFleetModule` becomes a thin per-turn dispatcher (`simple` → CoT,
`rlm` → `RLMAgent`) and is renamed since "escalating" no longer applies. This
composes with the `factory.py` decomposition: `_StreamingRLM` is the RLM
engine, `RLMAgent` is the agent that drives it.

## Considered Options

- **Keep `_run_rlm()` inside the renamed dispatcher.** Rejected: the method is
  ~880 lines with three RLM variants and deep retry logic; inlining it leaves
  the rlm path without a testable unit and blocks the delegation unification.
- **Delete `EscalatingFleetModule` entirely; `AgentRuntime` dispatches directly
  to `SimpleAgent`/`RLMAgent`.** Rejected for now: execution mode is per-turn
  (set via `set_execution_mode()` and per websocket message), so a single
  runtime instance must handle both modes across turns — a dispatcher is still
  needed. `AgentRuntime` could absorb that role later, but that is a separate,
  larger change.

## Consequences

- `RLMAgent` becomes the unit that owns Daytona binding, RLM variant selection,
  and recursion; it is independently testable with a mocked interpreter.
- `rlm_query()` becomes the single documented sub-agent spawning surface; the
  scattered `sub_rlm`/`delegate_to_rlm` paths are consolidated in Phase 2.3.
- The renamed dispatcher holds `self.respond` (CoT) and `self.rlm_agent`
  (`RLMAgent`); its `forward`/`aforward` are a two-branch mode switch.
- `_StreamingRLM` (factory.py) remains the engine layer; `RLMAgent` is the
  agent layer above it. The factory.py decomposition and `RLMAgent` extraction
  must stay coordinated so the engine/agent boundary stays clean.
