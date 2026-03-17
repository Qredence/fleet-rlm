# Fleet-RLM Documentation

Welcome to the `fleet-rlm` documentation. This documentation follows the [Diataxis framework](https://diataxis.fr/) for comprehensive coverage.

## Quick Links

| Category | Description |
|----------|-------------|
| [Architecture](architecture.md) | System architecture diagrams and data flows |
| [Tutorials](tutorials/index.md) | Learning-oriented guides for getting started |
| [How-to Guides](how-to-guides/index.md) | Task-oriented guides for specific goals |
| [Reference](reference/index.md) | Information-oriented technical documentation |
| [Explanation](explanation/index.md) | Understanding-oriented conceptual guides |

## Quick Start

```bash
uv tool install fleet-rlm
fleet web
```

Then open `http://localhost:8000`.

## Choose Your Path

### Use the Product

- [Installation Guide](how-to-guides/installation.md)
- [Runtime Settings](how-to-guides/runtime-settings.md)
- [Troubleshooting](how-to-guides/troubleshooting.md)

### Build Integrations

- [HTTP and WebSocket API](reference/http-api.md)
- [CLI Reference](reference/cli.md)
- [MCP Server Integration](how-to-guides/using-mcp-server.md)

### Understand the System

- [Architecture Overview](architecture.md)
- [Backend/Frontend Wiring Analysis](wiring-analysis.md)
- [Module Map](reference/module-map.md)
- [Codebase Map](reference/codebase-map.md)
- [Concepts](explanation/concepts.md)
- [Historical Snapshots](historical/index.md)

### Contribute

- [Contributing Guide](../CONTRIBUTING.md)
- [Post-restructure stabilization plan](superpowers/plans/2026-03-16-post-restructure-stabilization.md)

## Source-of-Truth Policy

When documentation conflicts with implementation, treat these as authoritative:

- **CLI truth**: `uv run fleet-rlm --help`
- **API truth**: `openapi.yaml`
- **WebSocket truth**: `src/fleet_rlm/server/routers/ws/api.py`

---

For a complete table of contents, see [SUMMARY.md](SUMMARY.md).
