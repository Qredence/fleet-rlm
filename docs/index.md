# Fleet-RLM Documentation

Welcome to the `fleet-rlm` documentation. The docs are organized roughly around
the [Diataxis framework](https://diataxis.fr/) and describe the maintained
product: an adaptive DSPy + Daytona workspace for recursive task execution.

## Quick Links

| Category | Description |
|----------|-------------|
| [Adaptive RLM Product Spec](explanation/product-spec.md) | User-facing product definition and capability model |
| [Architecture](architecture.md) | System architecture diagrams and data flows |
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
- [Backend/Frontend Wiring Analysis](wiring-analysis.md)
- [Phase 3 orchestration seams note](notes/phase-3-orchestration-seams.md)
- [Phase 4 outer orchestration note](notes/phase-4-outer-orchestration.md)
- [Phase 5 session orchestration note](notes/phase-5-session-orchestration.md)
- [Phase 6 terminal orchestration note](notes/phase-6-terminal-orchestration.md)
- [Phase 7/8 Agent Framework transition note](notes/phase-7-8-agent-framework-transition.md)
- [Phase 9 Agent host HITL migration note](notes/phase-9-agent-host-hitl-migration.md)
- [Phase 10 Agent host session migration note](notes/phase-10-agent-host-session-migration.md)
- [Phase 11 Agent host REPL bridge migration note](notes/phase-11-agent-host-repl-bridge.md)
- [Phase 12 DSPy recursive module + GEPA note](notes/phase-12-dspy-recursive-module-gepa.md)
- [Phase 13 recursive context assembly note](notes/phase-13-recursive-context-assembly.md)
- [Phase 14 recursive decomposition module note](notes/phase-14-recursive-decomposition-module.md)
- [Module Map](reference/module-map.md)
- [Codebase Map](reference/codebase-map.md)
- [Concepts](explanation/concepts.md)
- [Historical Snapshots](historical/index.md)

### Contribute

- [Contributing Guide](../CONTRIBUTING.md)
- [Developer setup](how-to-guides/developer-setup.md)
- [CLI reference](reference/cli.md)

## Source-of-Truth Policy

When documentation conflicts with implementation, treat these as authoritative:

- **CLI truth**: `uv run fleet-rlm --help` and `uv run fleet --help`
- **API truth**: `openapi.yaml`
- **WebSocket truth**: `src/fleet_rlm/api/routers/ws/endpoint.py` and adjacent helpers in `src/fleet_rlm/api/routers/ws/`

---

For a complete table of contents, see [SUMMARY.md](SUMMARY.md).
