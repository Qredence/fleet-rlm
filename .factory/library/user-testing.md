# User Testing

## Validation Surface

### Workspace shell and live execution
- Tool: `agent-browser`
- Entry: `http://127.0.0.1:8000/app/workspace`
- Verify: shell loads, runtime warning/remediation flows are coherent, live workspace execution succeeds after Daytona readiness repair, and trace/reasoning cards still render from streamed events

### Runtime diagnostics and settings
- Tools: `agent-browser`, `curl`, focused `pytest`
- Verify: `/health`, `/ready`, `/api/v1/runtime/status`, settings snapshot/load-save behavior, and LM/Daytona smoke-test controls/results

### Optimization
- Tools: `agent-browser`, focused `pytest`, `curl`
- Verify: optimization status/guidance rendering, form controls, and structured run responses for valid requests

### Shared backend contract
- Tools: focused `pytest`
- Verify: websocket execution envelope, Daytona request-control propagation, chat agent runtime behavior, and DSPy optimization/evaluation entrypoint compatibility

## Validation Readiness Dry Run

Dry run completed before mission start.

Observed results:
- `GET /health` returned `200`
- `GET /ready` returned `200`
- `GET /api/v1/runtime/status` returned `200`
- Browser navigation to `/app/workspace` succeeded
- A live websocket execution attempted to start but failed because Daytona volume `rlm-volume-dspy` was in `pending_create`
- The workspace/settings surfaces exposed runtime guidance, confirming the contract surface is reachable even though the live path is blocked

Required follow-up:
- The mission must repair Daytona readiness enough that a real workspace execution can complete during user testing

## Validation Concurrency

Machine profile observed during planning:
- CPU cores: `10`
- Memory: `16 GiB`
- Existing local processes already consume meaningful headroom (MLflow, Playwright, pytest, IDE/MCP helpers)

### Live workspace execution
- Max concurrent validators: `1`
- Rationale: the Daytona-backed execution path is the heaviest and depends on shared external/provider state

### Read-only UI/API checks
- Max concurrent validators: `3`
- Rationale: browser navigation and API/status checks are lighter, but still share the same local app/server resources

### Focused pytest contract lanes
- Run as serialized lanes unless the validator proves isolation is safe
- Rationale: websocket/runtime/provider tests share backend state and should remain deterministic
