# Python Backend Module Map

This document maps the current module relationships within `src/fleet_rlm/`.
It reflects the simplified backend split across `api/`, `cli/`, `runtime/`,
`integrations/`, and `scaffold/`.

## Layered Overview

```mermaid
graph TB
    CLI["cli/"]
    API["api/"]
    RUNTIME["runtime/"]
    INTEGRATIONS["integrations/"]
    SCAFFOLD["scaffold/"]
    UI["ui/dist"]

    CLI --> API
    CLI --> RUNTIME
    CLI --> INTEGRATIONS
    CLI --> SCAFFOLD

    API --> RUNTIME
    API --> INTEGRATIONS
    API --> UI

    RUNTIME --> INTEGRATIONS
```

## Runtime Surfaces

| Surface | Entry point | Primary dependencies |
| --- | --- | --- |
| `fleet` | `cli/main.py` | `cli/fleet_cli.py`, `cli/terminal/*` |
| `fleet-rlm` | `cli/fleet_cli.py` | `cli/commands/*`, `cli/runners.py`, `integrations/config/*` |
| FastAPI server | `api/main.py:create_app` | `api/routers/*`, `api/auth/*`, `integrations/database/*`, `integrations/observability/*` |
| FastMCP server | `integrations/mcp/server.py:create_mcp_server` | `cli/runners.py`, `runtime/agent/*`, `runtime/config.py` |

## Shared Runtime Map

```mermaid
graph LR
    CHAT["runtime/agent/chat_agent.py"]
    RLM["runtime/agent/recursive_runtime.py"]
    SIG["runtime/agent/signatures.py"]
    EXEC["runtime/execution/interpreter.py"]
    STREAM["runtime/execution/streaming.py"]
    STREAM_CTX["runtime/execution/streaming_context.py"]
    TOOLS["runtime/tools/"]
    CONTENT["runtime/content/"]
    MODELS["runtime/models/"]
    PROVIDERS["integrations/daytona/"]

    CHAT --> SIG
    CHAT --> TOOLS
    CHAT --> EXEC
    CHAT --> STREAM
    CHAT --> STREAM_CTX
    CHAT --> RLM
    TOOLS --> CONTENT
    EXEC --> PROVIDERS
    STREAM --> MODELS
```

### Key dependencies

| From | To | Purpose |
| --- | --- | --- |
| `runtime/agent/chat_agent.py` | `runtime/tools/*` | Tool list assembly and tool dispatch |
| `runtime/agent/chat_agent.py` | `runtime/execution/interpreter.py` | Sandbox-backed execution |
| `runtime/agent/recursive_runtime.py` | `runtime/execution/*` | Recursive delegation and streamed child turns |
| `runtime/tools/*` | `runtime/content/*` | Chunking, grounding, document, and log workflows |
| `runtime/execution/*` | `integrations/daytona/*` | Daytona interpreter/session backend integration |

## API and WebSocket Map

```mermaid
graph LR
    APP["api/main.py"]
    ROUTERS["api/routers/"]
    WS_ENDPOINT["api/routers/ws/endpoint.py"]
    WS_STREAM["api/routers/ws/stream.py"]
    WS_SESSION["api/routers/ws/session.py"]
    WS_COMMANDS["api/routers/ws/commands.py"]
    WS_HELPERS["api/routers/ws/* helpers"]
    EXEC["api/events/"]
    AUTH["api/auth/"]
    RUNTIME["runtime/"]
    INTEGRATIONS["integrations/"]

    APP --> ROUTERS
    ROUTERS --> AUTH
    ROUTERS --> WS_ENDPOINT
    ROUTERS --> EXEC
    ROUTERS --> INTEGRATIONS
    WS_ENDPOINT --> WS_SESSION
    WS_ENDPOINT --> WS_STREAM
    WS_ENDPOINT --> WS_HELPERS
    WS_STREAM --> WS_COMMANDS
    WS_STREAM --> WS_HELPERS
    WS_STREAM --> RUNTIME
    WS_SESSION --> RUNTIME
    WS_SESSION --> INTEGRATIONS
```

### Key dependencies

| From | To | Purpose |
| --- | --- | --- |
| `api/main.py` | `api/bootstrap.py` | Runtime bootstrap lifecycle, critical startup, and optional warmup scheduling |
| `api/bootstrap.py` | `integrations/database/*` | Database manager and repository setup |
| `api/bootstrap.py` | `integrations/observability/*` | PostHog and MLflow lifecycle setup |
| `api/routers/ws/*` | `runtime/agent/*` | Shared runtime execution |
| `api/routers/ws/commands.py`, `hitl.py` | `runtime/agent/*` | Command dispatch and HITL command handling |
| `api/routers/ws/lifecycle.py`, `turn_setup.py`, `turn_lifecycle.py` | `integrations/database/*`, `api/events/*` | Run/turn lifecycle orchestration |
| `api/routers/ws/persistence.py`, `manifest.py`, `artifacts.py` | `integrations/database/*` | Durable state, manifest, and artifact persistence |
| `api/routers/ws/errors.py`, `failures.py`, `loop_exit.py`, `task_control.py`, `terminal.py`, `completion.py` | `runtime/models/*`, `api/events/*` | Failure handling, cancellation, terminal event shaping, and final summaries |
| `api/routers/ws/types.py` | `integrations/daytona/*` | Daytona-specific request normalization |
| `api/events/*` | `runtime/models/*` | Trace/event shaping |
| `api/runtime_services/settings.py` | `integrations/config/*` | Runtime settings mutation and env/config synchronization |
| `api/runtime_services/diagnostics.py` | `integrations/config/*`, `integrations/daytona/*` | Runtime diagnostics, status, and provider connectivity tests |
| `api/runtime_services/volumes.py` | `integrations/daytona/volumes.py` | Volume browsing |

## Integration Packages

| Package | Role | Notable files |
| --- | --- | --- |
| `integrations/config/` | App/env/runtime settings | `env.py`, `runtime_settings.py`, `_env_utils.py`, `config.yaml` |
| `integrations/database/` | Persistence boundary | `engine.py`, `models.py`, `repository.py`, `types.py` |
| `integrations/mcp/` | FastMCP server surface | `server.py` |
| `integrations/observability/` | Telemetry and tracing | `posthog_callback.py`, `mlflow_runtime.py`, `mlflow_traces.py`, `trace_context.py` |
| `runtime/quality/` | DSPy evaluation and optimization | `dspy_evaluation.py`, `gepa_optimization.py`, `mlflow_evaluation.py`, `mlflow_optimization.py`, `workspace_metrics.py`, `scorers.py` |
| `integrations/daytona/` | Daytona interpreter backend | `agent.py`, `bridge.py`, `interpreter.py`, `runtime.py`, `volumes.py`, `config.py`, `diagnostics.py`, `types.py`, `interpreter_execution.py`, `interpreter_assets.py`, `runtime_helpers.py` |

## Verification

The package graph above was checked against the live tree with:

```bash
# from repo root
find src/fleet_rlm -maxdepth 2 -type d | sort
rg --files src/fleet_rlm
rg -n "^from fleet_rlm\\.|^import fleet_rlm\\." src/fleet_rlm
```
