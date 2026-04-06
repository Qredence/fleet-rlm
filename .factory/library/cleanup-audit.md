# Cleanup Audit

Current simplification audit for the operational Python package under `src/fleet_rlm`.

---

## Package size summary

- Total Python files under `src/fleet_rlm`: ~215
- Primary complexity concentration: `runtime/`, `integrations/providers/daytona/`, `api/routers/ws/`, and `cli/runtime_factory.py`

## Largest complexity hotspots

- `src/fleet_rlm/runtime/agent/chat_agent.py`
- `src/fleet_rlm/runtime/execution/streaming.py`
- `src/fleet_rlm/api/routers/ws/stream.py`
- `src/fleet_rlm/api/routers/ws/runtime.py`
- `src/fleet_rlm/cli/runtime_factory.py`
- `src/fleet_rlm/integrations/providers/daytona/runtime.py`
- `src/fleet_rlm/integrations/providers/daytona/interpreter.py`
- `src/fleet_rlm/integrations/providers/daytona/agent.py`
- `src/fleet_rlm/api/runtime_services/diagnostics.py`
- `src/fleet_rlm/integrations/database/repository.py`

## High-confidence simplification candidates

- Backend runtime assembly that currently routes through CLI-oriented factories/runners
- Low-value package/root compatibility exports and alias modules
- Websocket lifecycle helpers whose names obscure the dominant flow
- Daytona compatibility wrappers that undersell provider ownership or duplicate shared-runtime concepts
- Thin repository/observability facades that add indirection without clarifying ownership

## Handle with caution

- `src/fleet_rlm/scaffold/*`: packaged CLI behavior, not the primary target of this mission
- `src/fleet_rlm/ui/dist/*`: generated packaging output, not a handwritten simplification target
- Canonical route, websocket, CLI, and browser-shell surfaces captured in the validation contract
- `src/fleet_rlm/api/bootstrap_observability.py`: already has an unrelated working-tree modification in the user’s checkout

## Reachability reminders

Workers must collect evidence from imports, tests, CLI help output, route registration, and browser/API behavior before deleting a seam. If a module is still referenced by tests or a public entrypoint, either migrate those references inside the feature or return to the orchestrator with the dependency map.
