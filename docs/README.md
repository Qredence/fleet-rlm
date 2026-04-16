# fleet-rlm Documentation

`fleet-rlm` is a Daytona-backed recursive DSPy workbench. The maintained product is the live workbench, the durable volumes browser, the optimization surface, runtime settings and diagnostics, and the session history and replay view. This documentation mirrors that current product and keeps migration history separate.

This documentation is for both:

- users operating `fleet-rlm` locally or in deployment workflows
- contributors extending the current runtime, transport, or frontend shell

## Quickstart

```bash
uv sync --all-extras
uv run fleet web
```

Then open `http://localhost:8000`.

## Current Docs

- [Product Spec](explanation/product-spec.md)
- [Architecture Overview](architecture.md)
- [Reference Index](reference/index.md)
- [Explanation Index](explanation/index.md)
- [Frontend Product Surface Guide](guides/frontend-product-surface.md)
- [Optimization Page Spec](specs/optimization-page.md)
- [Wiring Analysis](wiring-analysis.md)
- [Runtime Settings](how-to-guides/runtime-settings.md)
- [Deploying the API Server](how-to-guides/deploying-server.md)
- [Frontend/Backend Integration](reference/frontend-backend-integration.md)

## Use the Product

- [Installation](how-to-guides/installation.md)
- [Runtime settings](how-to-guides/runtime-settings.md)
- [Troubleshooting](how-to-guides/troubleshooting.md)
- [LiteLLM proxy model availability](litellm-models.md)

## Build and Integrate

- [HTTP and WebSocket API](reference/http-api.md)
- [Python API](reference/python-api.md)
- [CLI reference](reference/cli.md)
- [Using the MCP server](how-to-guides/using-mcp-server.md)

## Understand the System

- [Architecture overview](architecture.md)
- [Concepts](explanation/concepts.md)
- [User interaction flows](explanation/user-flows.md)
- [Component UML](explanation/component-uml.md)

## Historical Notes

- [Historical snapshots](historical/index.md)
- [Architecture and migration history](historical/index.md#architecture-and-migration-history)

## Documentation Map

- [Tutorials](tutorials/index.md)
- [How-to Guides](how-to-guides/index.md)
- [Reference](reference/index.md)
- [Explanation](explanation/index.md)
- [Complete table of contents](SUMMARY.md)

## Source of Truth

When docs conflict with implementation, trust:

- CLI truth: `uv run fleet-rlm --help` and `uv run fleet --help`
- API truth: `openapi.yaml`
- WebSocket truth: `src/fleet_rlm/api/routers/ws/endpoint.py` and adjacent helpers in `src/fleet_rlm/api/routers/ws/`
- Runtime truth: `src/fleet_rlm/runtime/agent/chat_agent.py`, `src/fleet_rlm/runtime/agent/recursive_runtime.py`, `src/fleet_rlm/integrations/daytona/interpreter.py`, and `src/fleet_rlm/integrations/daytona/runtime.py`
