# Explicit Execution Modes: simple and rlm

## Context

The backend previously routed every chat turn through a hidden two-stage
auto-escalation state machine: a deterministic router (`rlm_routing.py` /
`context_routing.py`) plus an LLM classifier (`RouteTurnSignature` in
`EscalatingFleetModule`) that picked among `direct`, `tools`, and `rlm` for
any turn sent as `execution_mode="auto"`. A third route —
`AgentRuntime(use_escalation=False)` building a bare `FleetAgent` (`dspy.ReAct`)
loop — existed as an undocumented "tools-without-recursion" escape hatch. The
public contract exposed `auto` / `rlm_only` / `tools_only`, while the runtime
also accepted `rlm` without declaring it in the schema.

## Decision

Collapse the public execution contract to two canonical modes, chosen
explicitly by the caller with no auto-escalation:

- **`simple`** — one `dspy.ChainOfThought(RLMReActChatSignature)` response. No
  tools, no sandbox, no recursion. Reuses the existing `self.respond` module
  (also the RLM failure-fallback). Default for backend, frontend, websocket
  schema, and runtime state.
- **`rlm`** — `dspy.RLM` through Daytona with the full tool surface and
  recursion. After the caller chooses `rlm`, deterministic sub-selection among
  `url_document_rlm`, `large_context_rlm`, and standard `rlm` remains internal
  and based on input shape (URL present, large staged context); `simple` never
  enters this path.

Remove `use_escalation`, `FleetAgent`, `FleetAgentSignature`, the bare-React
build branch, `RouteTurnSignature`, `self.route`, `self._react`, `_route_turn`,
`_run_react`/`_arun_react`, and the `_react_fallback`. `AgentRuntime` always
builds `EscalatingFleetModule`; mode is controlled solely by `execution_mode`.

Legacy values are accepted for one release with deprecation warnings:
`auto` → `simple`, `tools_only` → `simple` (behavior change: tool access
removed; warning directs users to `rlm`), `rlm_only` → `rlm`. Runtime
observability payloads carry `execution_mode`, `legacy_execution_mode`, and
`execution_mode_warning`.

## Considered Options

- **Keep `use_escalation=False` as an escape hatch.** Rejected: it is exactly
  the "tools-without-recursion" route the two-mode model removes, and leaving
  it contradicts the sole-contract goal.
- **Map `tools_only` → `rlm`.** Rejected: `tools_only` historically meant "no
  recursion", so mapping to `rlm` silently adds recursion — a larger semantic
  shift than losing tools. The deprecation warning gives users a release to
  migrate to `rlm` explicitly.
- **Hard-cut legacy values.** Rejected for the compatibility window: the
  websocket contract and frontend selector are public surfaces.

## Consequences

- The frontend mode selector shows exactly two choices (Simple / RLM); the
  Tools option is removed.
- `FleetAgent`/`FleetAgentSignature` and the `tools_only`-round-trip frontend
  test must be deleted; tests asserting auto-routing are rewritten to assert
  the explicit mode contract.
- Generated OpenAPI and `rlm-api` frontend types must be regenerated via
  `make api-sync`, never hand-edited.
- Large context or URL intent plus `simple` stays `simple` — no auto-routing.
  Callers wanting that behavior must select `rlm`.
