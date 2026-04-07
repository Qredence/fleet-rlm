# fleet-rlm Documentation

`fleet-rlm` provides an adaptive recursive language model workspace with a Web
UI, API, and optional MCP server. The maintained product path is DSPy-native
and Daytona-backed, with one shared workspace and transport contract.

This documentation is for both:

- users operating `fleet-rlm` locally or in deployment workflows
- contributors building integrations, extending runtime behavior, or maintaining the codebase

## Quickstart

```bash
uv init
uv add fleet-rlm
uv run fleet web
```

Then open `http://localhost:8000`.

Next steps:

- [Installation details](how-to-guides/installation.md)
- [Runtime settings](how-to-guides/runtime-settings.md)
- [Deploying the API server](how-to-guides/deploying-server.md)

## Choose Your Path

## Use the product

- [Installation](how-to-guides/installation.md)
- [Adaptive RLM Product Spec](explanation/product-spec.md)
- [Runtime settings](how-to-guides/runtime-settings.md)
- [LiteLLM proxy model availability](litellm-models.md)
- [Deploying the API server](how-to-guides/deploying-server.md)
- [Troubleshooting](how-to-guides/troubleshooting.md)

## Contribute to the project

- [Contributing guide](../CONTRIBUTING.md)

## Build integrations

- [HTTP and WebSocket API](reference/http-api.md)
- [Python API](reference/python-api.md)
- [CLI reference](reference/cli.md)
- [Using the MCP server](how-to-guides/using-mcp-server.md)

## Understand architecture

- [Architecture overview](architecture.md)
- [Concepts](explanation/concepts.md)
- [User interaction flows](explanation/user-flows.md)
- [Component UML](explanation/component-uml.md)

## Documentation Map (Diataxis)

- [Tutorials](tutorials/index.md)
- [How-to Guides](how-to-guides/index.md)
- [Reference](reference/index.md)
- [Explanation](explanation/index.md)

## Source-of-Truth Policy

When docs conflict with implementation, treat these as authoritative:

- CLI truth: `uv run fleet-rlm --help` and `uv run fleet --help`
- API truth: `openapi.yaml`
- WebSocket truth: `src/fleet_rlm/api/routers/ws/endpoint.py` and adjacent helpers in `src/fleet_rlm/api/routers/ws/`

## Archive Note

Historical docs are archived and non-operational:

- Legacy planning docs are stored under the local-only `plans/archive/docs-legacy/` path.
