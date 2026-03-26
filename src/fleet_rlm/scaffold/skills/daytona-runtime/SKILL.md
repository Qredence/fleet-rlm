---
name: daytona-runtime
description: Explain and operate fleet-rlm's Daytona-backed runtime path from Claude Code. Use when working with daytona_pilot, repo context staging, Daytona durable volume layout, or native Daytona smoke validation.
---

# Daytona Runtime — fleet-rlm Translation

This skill explains the Daytona-specific side of `fleet-rlm` as it exists
today.

## Key Invariants

- `daytona_pilot` uses the same shared ReAct plus `dspy.RLM` runtime as `modal_chat`.
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
- Websocket surfaces stay `/api/v1/ws/chat` and `/api/v1/ws/execution`

## Operational Guidance

1. Run `fleet-rlm daytona-smoke` before using `daytona_pilot` in the workspace.
2. Treat `DAYTONA_TARGET` as SDK routing/config only, not as a workspace id or volume name.
3. Keep Daytona-specific reasoning in the provider boundary under `integrations/providers/daytona/*`.
4. When debugging durable Daytona persistence, inspect `/home/daytona/memory/{memory,artifacts,buffers,meta}`. Treat the live workspace as transient.

## Claude Code Delegation

- Use `rlm-specialist` when a Claude Code task needs Daytona-aware runtime debugging.
- Use `rlm-orchestrator` when the goal is repo-aware workbench execution or long-context processing in the Daytona path.
