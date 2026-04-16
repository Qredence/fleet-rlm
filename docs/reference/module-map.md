# Python Backend Module Map

This document maps the current module relationships within `src/fleet_rlm/` with the runtime core centered explicitly.

## Layered Overview

```mermaid
graph TB
    CLI["cli/"] --> API["api/"]
    CLI --> WORKER["worker/"]
    CLI --> RUNTIME["runtime/"]
    CLI --> INTEGRATIONS["integrations/"]
    CLI --> SCAFFOLD["scaffold/"]

    API --> HOST["agent_host/"]
    API --> RUNTIME
    API --> INTEGRATIONS
    API --> UI["ui/"]

    HOST --> WORKER
    WORKER --> RUNTIME
    RUNTIME --> DAYTONA["integrations/daytona/"]
    RUNTIME --> QUALITY["runtime/quality/"]
```

## Runtime Surfaces

| Surface | Entry point | Primary dependencies |
| --- | --- | --- |
| `fleet` | `cli/main.py` | `cli/fleet_cli.py`, `cli/terminal/*` |
| `fleet-rlm` | `cli/fleet_cli.py` | `cli/commands/*`, `cli/runners.py`, `integrations/config/*` |
| FastAPI server | `api/main.py:create_app` | `api/routers/*`, `api/auth/*`, `agent_host/*`, `integrations/database/*`, `integrations/observability/*` |
| FastMCP server | `integrations/mcp/server.py:create_mcp_server` | `cli/runners.py`, `runtime/agent/*`, `runtime/config.py` |

## Core Execution Path

```mermaid
graph LR
    REQUEST["Workspace task request"] --> WORKER["worker/streaming.py"]
    WORKER --> AGENT["runtime/factory.py"]
    AGENT --> CHAT["runtime/agent/chat_agent.py"]
    CHAT --> EXEC["runtime/execution/*"]
    EXEC --> DAYTONA_INTERPRETER["integrations/daytona/interpreter.py"]
    EXEC --> DAYTONA_RUNTIME["integrations/daytona/runtime.py"]
    CHAT --> MODELS["runtime/models/*"]
    CHAT --> QUALITY["runtime/quality/*"]
```

### Key dependencies

| From | To | Purpose |
| --- | --- | --- |
| `worker/*` | `runtime/agent/*` | Stream prepared workspace work through the shared runtime agent |
| `runtime/agent/chat_agent.py` | `runtime/tools/*` | Tool list assembly and tool dispatch |
| `runtime/agent/chat_agent.py` | `runtime/execution/*` | Streaming turn execution and interpreter support |
| `runtime/agent/recursive_runtime.py` | `integrations/daytona/*` | Recursive child execution over the Daytona substrate |
| `runtime/execution/*` | `integrations/daytona/interpreter.py`, `integrations/daytona/runtime.py` | Stateful interpreter/session backend integration |
| `runtime/models/*` | `runtime/agent/*` | Builder, registry, and runtime-model exports |
| `runtime/quality/*` | `runtime/agent/*`, `runtime/models/*` | Offline evaluation and optimization against the live runtime graph |

## API and Host Map

```mermaid
graph LR
    APP["api/main.py"]
    ROUTERS["api/routers/"]
    WS_ENDPOINT["api/routers/ws/endpoint.py"]
    WS_STREAM["api/routers/ws/stream.py"]
    WS_SESSION["api/routers/ws/session.py"]
    WS_TURN_SETUP["api/routers/ws/turn_setup.py"]
    WS_COMPLETION["api/routers/ws/completion.py"]
    RUNTIME_SERVICES["api/runtime_services/*"]
    EVENTS["api/events/*"]
    HOST_WORKFLOW["agent_host/workflow.py"]
    HOST_POLICY["agent_host/hitl_flow.py + terminal_flow.py"]
    WORKER["worker/*"]
    RUNTIME["runtime/*"]

    APP --> ROUTERS
    ROUTERS --> WS_ENDPOINT
    ROUTERS --> RUNTIME_SERVICES
    ROUTERS --> EVENTS
    WS_ENDPOINT --> WS_STREAM
    WS_ENDPOINT --> WS_SESSION
    WS_ENDPOINT --> WS_TURN_SETUP
    WS_ENDPOINT --> WS_COMPLETION
    WS_STREAM --> HOST_WORKFLOW
    HOST_WORKFLOW --> HOST_POLICY
    HOST_WORKFLOW --> WORKER
    WORKER --> RUNTIME
```

### Key dependencies

| From | To | Purpose |
| --- | --- | --- |
| `api/main.py` | `api/bootstrap.py` | Runtime bootstrap lifecycle, critical startup, and optional warmup scheduling |
| `api/routers/ws/*` | `agent_host/*` | Hosted execution, HITL policy, execution events, and startup/repl bridging |
| `agent_host/workflow.py` | `worker/*` | Hosted policy around the worker seam |
| `api/runtime_services/settings.py` | `integrations/config/*` | Runtime settings mutation and env/config synchronization |
| `api/runtime_services/diagnostics.py` | `integrations/config/*`, `integrations/daytona/*` | Runtime diagnostics, status, and provider connectivity tests |
| `api/runtime_services/volumes.py` | `integrations/daytona/volumes.py` | Volume browsing |
| `api/events/*` | `runtime/execution/streaming_context.py`, frontend workspace stores | Event shaping for passive execution subscriptions and workbench hydration |

## Integration Packages

| Package | Role | Notable files |
| --- | --- | --- |
| `integrations/config/` | App/env/runtime settings | `env.py`, `runtime_settings.py`, `_env_utils.py`, `config.yaml` |
| `integrations/database/` | Persistence boundary | `engine.py`, `models.py`, `repository.py`, `types.py` |
| `integrations/local_store.py` | Local sidecar persistence | session history, turn transcripts, optimization-run tracking |
| `integrations/mcp/` | FastMCP server surface | `server.py` |
| `integrations/observability/` | Telemetry and tracing | `posthog_callback.py`, `mlflow_runtime.py`, `mlflow_traces.py`, `trace_context.py` |
| `integrations/daytona/` | Daytona execution and workspace substrate | `interpreter.py`, `runtime.py`, `volumes.py`, `config.py`, `diagnostics.py`, `types.py`, `bridge.py`, `runtime_helpers.py` |
| `runtime/quality/` | DSPy evaluation and optimization | `dspy_evaluation.py`, `gepa_optimization.py`, `mlflow_evaluation.py`, `mlflow_optimization.py`, `workspace_metrics.py`, `scorers.py` |

## Verification

The package graph above was checked against the live tree with:

```bash
# from repo root
find src/fleet_rlm -maxdepth 2 -type d | sort
rg --files src/fleet_rlm
rg -n "^from fleet_rlm\\.|^import fleet_rlm\\." src/fleet_rlm
```
