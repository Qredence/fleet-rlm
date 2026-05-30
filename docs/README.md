# fleet-rlm Documentation

`fleet-rlm` is a Daytona-backed recursive DSPy workbench. The maintained product surfaces are the live workbench, the durable volumes browser, and runtime settings and diagnostics. This documentation mirrors that current product and keeps migration history separate.

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
- [Frontend Product Surface Guide](explanation/frontend-product-surface.md)
- [Wiring Analysis](explanation/wiring-analysis.md)
- [Runtime Settings](how-to-guides/runtime-settings.md)
- [Codex Local Environment](how-to-guides/codex-environment.md)
- [Agent Harness](agent-harness/README.md)
- [Deploying the API Server](how-to-guides/deploying-server.md)
- [Frontend/Backend Integration](reference/frontend-backend-integration.md)

## API Reference Surfaces

- [Sandbox API](reference/sandbox-api.md) — Daytona sandbox lifecycle management
- [Runs API](reference/runs-api.md) — Execution trace step browsing

## Use the Product

- [Installation](how-to-guides/installation.md)
- [Runtime settings](how-to-guides/runtime-settings.md)
- [Troubleshooting](how-to-guides/troubleshooting.md)
- [LiteLLM proxy model availability](reference/litellm-models.md)

## Build and Integrate

- [HTTP and WebSocket API](reference/http-api.md)
- [Sandbox API](reference/sandbox-api.md)
- [Runs API](reference/runs-api.md)
- [Python API](reference/python-api.md)
- [CLI reference](reference/cli.md)

## Understand the System

- [Architecture overview](architecture.md)
- [Agent Harness](agent-harness/README.md)
- [Concepts](explanation/concepts.md)
- [User interaction flows](explanation/user-flows.md)
- [Component UML](explanation/component-uml.md)

## Documentation Map

- [Tutorials](tutorials/index.md)
- [How-to Guides](how-to-guides/index.md)
- [Reference](reference/index.md)
- [Explanation](explanation/index.md)
- [Complete table of contents](SUMMARY.md)
- [Agent Harness](agent-harness/README.md)

## Source of Truth

When docs conflict with implementation, trust:

- CLI truth: `uv run fleet-rlm --help` and `uv run fleet --help`
- API truth: `openapi.yaml`
- WebSocket truth: `src/fleet_rlm/api/routers/ws/endpoint.py` and adjacent helpers in `src/fleet_rlm/api/routers/ws/`
- Runtime truth: `src/fleet_rlm/runtime/agent/agent.py`, `src/fleet_rlm/runtime/agent/runtime.py`, and the Daytona facade/collaborators under `src/fleet_rlm/integrations/daytona/`
