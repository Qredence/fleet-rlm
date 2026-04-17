---
name: daytona-runtime
description: Explain and operate fleet-rlm's Daytona-backed runtime path from Claude Code. Use when working with daytona_pilot, repo context staging, Daytona durable volume layout, or native Daytona smoke validation.
---

# Daytona Runtime — fleet-rlm Translation

This skill explains the Daytona-specific side of `fleet-rlm` as it exists
today.

## Key Invariants

- `daytona_pilot` is the primary runtime path, built on the shared ReAct plus `dspy.RLM` backbone.
- Daytona is the interpreter backend, not a separate orchestration system.
- The native validation entrypoint is:

```bash
# from repo root
uv run fleet-rlm daytona-smoke --repo <url> [--ref <branch>]
```

## What Changes In Daytona Mode

- Runtime mode becomes `daytona_pilot`
- Requests may include:
  - `repo_url`
  - `repo_ref`
  - `context_paths`
  - `batch_concurrency`
- The durable mounted volume is rooted at `/home/daytona/memory`
- Canonical durable directories are `memory/`, `artifacts/`, `buffers/`, and `meta/`

## What Does Not Change

- The top-level conversational runtime is still `RLMReActChatAgent`
- Recursive work still flows through `dspy.RLM`
- The canonical websocket surface stays `/api/v1/ws/execution`

## Operational Guidance

1. Run `fleet-rlm daytona-smoke` before using `daytona_pilot` in the workspace.
2. Treat `DAYTONA_TARGET` as SDK routing/config only, not as a workspace id or volume name.
3. Keep Daytona-specific reasoning in the provider boundary under `integrations/daytona/*`.
4. When debugging durable Daytona persistence, inspect `/home/daytona/memory/{memory,artifacts,buffers,meta}`. Treat the live workspace as transient.

## Canonical Provider Modules

All Daytona-specific implementation lives under `src/fleet_rlm/integrations/daytona/`:

- `runtime.py` — `DaytonaSandboxRuntime` and `DaytonaSandboxSession` (canonical async contract)
- `interpreter.py` — `DaytonaInterpreter` (DSPy CodeInterpreter adapter) including execution helpers and delegate child interpreter building
- `bridge.py` — host callback bridge (`llm_query`, `llm_query_batched`, custom tools)
- `runtime/agent/chat_agent.py` — `RLMReActChatAgent` (canonical shared DSPy chat agent with Daytona workspace/session handling)
- `diagnostics.py` — structured runtime diagnostics and smoke validation
- `types.py` — consolidated Daytona types plus chat/session normalization helpers
- `volumes.py` — Daytona volume browsing
- `config.py` — `ResolvedDaytonaConfig` resolution
- `runtime_helpers.py`, `interpreter_assets.py` — internal utilities

Deleted module paths such as `state.py` and `snapshots.py` are intentionally unsupported; use the owner modules above.

## Session Manifest Path

Durable session manifests live under:

```
meta/workspaces/<workspace_id>/users/<user_id>/react-session-<session_id>.json
```

Legacy path (`workspaces/...`) is still read as a migration fallback.

## Claude Code Delegation

- Use `rlm-specialist` when a Claude Code task needs Daytona-aware runtime debugging.
- Use `rlm-orchestrator` when the goal is repo-aware workbench execution or long-context processing in the Daytona path.
