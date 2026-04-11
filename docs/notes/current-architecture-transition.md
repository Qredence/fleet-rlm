# Current Architecture and Transition Note

This note captures the current architecture framing to use when updating docs or planning cleanup work.

## Current framing

Describe the backend in this order:

1. **Thin FastAPI/WebSocket transport**
2. **Narrow but real Agent Framework outer orchestration host**
3. **Daytona-backed recursive DSPy worker/runtime core**

Add these explicit qualifiers when needed:

- `agent_host/` is real, but intentionally narrow.
- `orchestration_app/` is a transitional compatibility layer.
- `api/orchestration/` contains compatibility shims.
- `runtime/agent/` plus `integrations/daytona/` are the long-term core.
- `runtime/quality/` is the offline evaluation/optimization layer.

## Read order for contributors

When trying to understand the system, start here:

1. `src/fleet_rlm/runtime/agent/chat_agent.py`
2. `src/fleet_rlm/runtime/agent/recursive_runtime.py`
3. `src/fleet_rlm/integrations/daytona/interpreter.py`
4. `src/fleet_rlm/integrations/daytona/runtime.py`
5. `src/fleet_rlm/worker/*`
6. `src/fleet_rlm/agent_host/workflow.py`
7. `src/fleet_rlm/api/main.py`
8. `src/fleet_rlm/api/routers/ws/*`
9. `src/fleet_rlm/orchestration_app/*`
10. `src/fleet_rlm/api/orchestration/*`

## Cleanup goals for the first pass

Keep the first pass about structure and discoverability only.

### Targeted cleanup checklist

- [ ] Reduce compatibility clutter in `src/fleet_rlm/api/orchestration/*`
- [ ] Reduce wrapper clutter under `src/fleet_rlm/api/routers/ws/*`
- [ ] Shrink `src/fleet_rlm/orchestration_app/*` toward only still-needed transition seams
- [ ] Remove stale ownership notes and deleted-file references from docs
- [ ] Keep one current architecture overview and one current transition note linked from the docs index

### Out of scope for the first pass

- [ ] No redesign
- [ ] No broad refactor
- [ ] No runtime behavior rewrite
- [ ] No movement of cognition into orchestration
- [ ] No movement of Daytona execution or memory semantics into transport or host state
- [ ] No websocket/public contract changes disguised as cleanup

## Documentation deliverables

The stable doc set for this framing should be:

- one current architecture overview (`docs/architecture.md`)
- one focused codebase map (`docs/reference/codebase-map.md`)
- one backend module map (`docs/reference/module-map.md`)
- one migration-status / transitional-structure note (this file)
