# Fleet-RLM Documentation

Welcome to the `fleet-rlm` documentation. The docs are organized roughly around the [Diataxis framework](https://diataxis.fr/) and describe the maintained product: a Daytona-backed recursive DSPy workspace with a thin FastAPI/WebSocket transport and a narrow Agent Framework host around it.

## Quick Links

| Category | Description |
|----------|-------------|
| [Adaptive RLM Product Spec](explanation/product-spec.md) | User-facing product definition and capability model |
| [Architecture](architecture.md) | Current architecture overview and layer ownership |
| [Current Architecture and Transition Note](notes/current-architecture-transition.md) | Migration status and cleanup guidance for transitional layers |
| [Codebase Map](reference/codebase-map.md) | Focused backend ownership map |
| [Module Map](reference/module-map.md) | Current backend module relationships |
| [Tutorials](tutorials/index.md) | Learning-oriented guides for getting started |
| [How-to Guides](how-to-guides/index.md) | Task-oriented guides for specific goals |
| [Reference](reference/index.md) | Information-oriented technical documentation |
| [Explanation](explanation/index.md) | Understanding-oriented conceptual guides |

## Quick Start

```bash
uv init
uv add fleet-rlm
uv run fleet web
```

Then open `http://localhost:8000`.

## Choose Your Path

### Use the Product

- [Installation Guide](how-to-guides/installation.md)
- [Adaptive RLM Product Spec](explanation/product-spec.md)
- [Runtime Settings](how-to-guides/runtime-settings.md)
- [Troubleshooting](how-to-guides/troubleshooting.md)

### Build Integrations

- [HTTP and WebSocket API](reference/http-api.md)
- [CLI Reference](reference/cli.md)
- [MCP Server Integration](how-to-guides/using-mcp-server.md)

### Understand the System

- [Architecture Overview](architecture.md)
- [Current Architecture and Transition Note](notes/current-architecture-transition.md)
- [Backend Codebase Map](reference/codebase-map.md)
- [Python Backend Module Map](reference/module-map.md)
- [Backend/Frontend Wiring Analysis](wiring-analysis.md)
- [Concepts](explanation/concepts.md)
- [Historical Snapshots](historical/index.md)
- [Phase-by-phase migration notes](historical/index.md#architecture-and-migration-history)

### Contribute

- [Contributing Guide](../CONTRIBUTING.md)
- [Developer setup](how-to-guides/developer-setup.md)
- [CLI reference](reference/cli.md)

## Source-of-Truth Policy

When documentation conflicts with implementation, treat these as authoritative:

- **CLI truth**: `uv run fleet-rlm --help` and `uv run fleet --help`
- **API truth**: `openapi.yaml`
- **WebSocket truth**: `src/fleet_rlm/api/routers/ws/endpoint.py` and adjacent helpers in `src/fleet_rlm/api/routers/ws/`
- **Runtime core truth**: `src/fleet_rlm/runtime/agent/chat_agent.py`, `src/fleet_rlm/runtime/agent/recursive_runtime.py`, `src/fleet_rlm/integrations/daytona/interpreter.py`, and `src/fleet_rlm/integrations/daytona/runtime.py`

---

For a complete table of contents, see [SUMMARY.md](SUMMARY.md).
